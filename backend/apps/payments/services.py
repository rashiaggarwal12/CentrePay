"""Payment business logic: creating payment links and applying gateway events.

Locking order everywhere: WebhookEvent (if any) -> Invoice -> Payment. Every path that
changes money locks the invoice row first, so two payments for the same invoice are
applied one after the other, never interleaved.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import audit, snapshot
from apps.billing import state_machine
from apps.billing.models import Invoice, InvoiceStatus
from apps.billing.services import INVOICE_FIELDS
from apps.common.exceptions import (
    BusinessValidationError,
    IdempotencyKeyReused,
    InvalidTransition,
    PaymentGatewayError,
    PaymentGatewayNotConfigured,
    RequestInProgress,
)
from apps.ledger.models import LedgerEntry
from apps.reconciliation.services import Kind, raise_issue

from .gateway import GatewayError, GatewayNotConfigured, get_gateway
from .models import Payment, PaymentAttempt

logger = logging.getLogger(__name__)

COLLECTABLE_STATUSES = {InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID}
MIN_LINK_AMOUNT_PAISE = 100  # Razorpay minimum is ₹1


# --- Collect (create payment link) ------------------------------------------------------------


def collect(
    invoice_id: int, *, staff, idempotency_key: str, amount_paise: int | None = None
) -> tuple[PaymentAttempt, bool]:
    """Get a payment link for an invoice. Returns (attempt, created).

    Safe to retry: the same Idempotency-Key always returns the same attempt, and if
    the first call died before the gateway answered, the retry finishes the job.
    """
    if not idempotency_key or len(idempotency_key) > 64:
        raise BusinessValidationError("An Idempotency-Key header (max 64 chars) is required.")

    existing = PaymentAttempt.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return _replay(existing, invoice_id, amount_paise), False

    try:
        attempt, created = _reserve_attempt(invoice_id, staff, idempotency_key, amount_paise)
    except IntegrityError:
        # A concurrent request with the same key won the insert (double tap).
        existing = PaymentAttempt.objects.get(idempotency_key=idempotency_key)
        return _replay(existing, invoice_id, amount_paise), False

    if created:
        # The gateway call happens outside the transaction: never hold a row lock
        # while waiting on the network.
        _ensure_link(attempt, recovering=False)
    return attempt, created


def _replay(attempt: PaymentAttempt, invoice_id: int, amount_paise: int | None) -> PaymentAttempt:
    if attempt.invoice_id != invoice_id or (
        amount_paise is not None and amount_paise != attempt.amount_paise
    ):
        raise IdempotencyKeyReused()
    if attempt.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.FAILED):
        if not _claim_for_recovery(attempt):
            attempt.refresh_from_db()
            if attempt.status in (PaymentAttempt.Status.PENDING, PaymentAttempt.Status.FAILED):
                raise RequestInProgress()
            return attempt  # the other request just finished
        _ensure_link(attempt, recovering=True)
    return attempt


# A pending attempt younger than this may still have a gateway call in flight
# (the client timeout is 10s), so a concurrent retry must not start another one.
PENDING_STALE_AFTER = timedelta(seconds=30)


def _claim_for_recovery(attempt: PaymentAttempt) -> bool:
    """Atomically take ownership of a failed or abandoned attempt. Exactly one of any
    number of concurrent retries wins; the rest are told the request is in progress."""
    now = timezone.now()
    claimed = (
        PaymentAttempt.objects.filter(pk=attempt.pk)
        .filter(
            Q(status=PaymentAttempt.Status.FAILED)
            | Q(status=PaymentAttempt.Status.PENDING, updated_at__lt=now - PENDING_STALE_AFTER)
        )
        .update(status=PaymentAttempt.Status.PENDING, updated_at=now)
    )
    return bool(claimed)


@transaction.atomic
def _reserve_attempt(invoice_id, staff, idempotency_key, amount_paise):
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    if invoice.status not in COLLECTABLE_STATUSES:
        raise InvalidTransition(
            f"Cannot collect payment on an invoice that is {invoice.get_status_display().lower()}."
        )
    due = invoice.amount_due_paise
    amount = due if amount_paise is None else amount_paise
    if amount < MIN_LINK_AMOUNT_PAISE:
        raise BusinessValidationError("Amount must be at least ₹1.00.")
    if amount > due:
        raise BusinessValidationError(f"Amount exceeds the amount due ({due} paise).")

    # A second staff member (or a second tap with a fresh key) collecting the same
    # amount gets the link that's already showing, instead of a second live QR that
    # could let the customer pay twice.
    reusable = (
        invoice.payment_attempts.filter(
            status=PaymentAttempt.Status.CREATED,
            amount_paise=amount,
            expires_at__gt=timezone.now() + timedelta(minutes=2),
        )
        .order_by("-created_at")
        .first()
    )
    if reusable:
        return reusable, False

    attempt = PaymentAttempt.objects.create(
        invoice=invoice,
        amount_paise=amount,
        idempotency_key=idempotency_key,
        reference_id=f"cp_{uuid.uuid4().hex[:24]}",
        created_by=staff,
    )
    audit("payment_attempt.created", attempt, actor=staff.user)
    return attempt, True


def _ensure_link(attempt: PaymentAttempt, *, recovering: bool) -> None:
    """Make sure the attempt has a live Razorpay link, creating or recovering it."""
    try:
        gateway = get_gateway()
    except GatewayNotConfigured as exc:
        _mark_attempt_failed(attempt, str(exc))
        raise PaymentGatewayNotConfigured() from exc

    try:
        link = gateway.find_payment_link_by_reference(attempt.reference_id) if recovering else None
        if link is None:
            link = gateway.create_payment_link(**_link_request(attempt))
    except GatewayError as exc:
        _mark_attempt_failed(attempt, str(exc))
        raise PaymentGatewayError() from exc

    PaymentAttempt.objects.filter(
        pk=attempt.pk, status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.FAILED]
    ).update(
        gateway_link_id=link["id"],
        short_url=link.get("short_url", ""),
        status=PaymentAttempt.Status.CREATED,
        expires_at=_from_unix(link.get("expire_by")),
        last_error="",
        updated_at=timezone.now(),
    )
    attempt.refresh_from_db()
    logger.info("payment_attempt.link_created attempt=%s link=%s", attempt.pk, link["id"])


def _link_request(attempt: PaymentAttempt) -> dict:
    invoice = attempt.invoice
    customer = {"name": invoice.customer.name, "contact": invoice.customer.phone}
    if invoice.customer.email:
        customer["email"] = invoice.customer.email
    expire_by = timezone.now() + timedelta(minutes=settings.PAYMENT_LINK_EXPIRY_MINUTES)
    return {
        "amount_paise": attempt.amount_paise,
        "reference_id": attempt.reference_id,
        "description": f"Invoice {invoice.number} - {invoice.centre.name}",
        "customer": customer,
        "expire_by": int(expire_by.timestamp()),
        "notes": {
            "invoice_id": str(invoice.pk),
            "invoice_number": invoice.number,
            "attempt_id": str(attempt.pk),
        },
    }


def _mark_attempt_failed(attempt: PaymentAttempt, error: str) -> None:
    PaymentAttempt.objects.filter(
        pk=attempt.pk, status__in=[PaymentAttempt.Status.PENDING, PaymentAttempt.Status.FAILED]
    ).update(status=PaymentAttempt.Status.FAILED, last_error=error[:1000])


def _from_unix(ts):
    return datetime.fromtimestamp(ts, tz=UTC) if ts else None


# --- Applying gateway events -------------------------------------------------------------------


def resolve_payment_target(payment: dict) -> tuple[int | None, PaymentAttempt | None]:
    """Work out which invoice (and attempt) a gateway payment belongs to."""
    known = (
        Payment.objects.filter(gateway_payment_id=payment["id"])
        .values_list("invoice_id", "attempt_id")
        .first()
    )
    if known:
        invoice_id, attempt_id = known
        attempt = PaymentAttempt.objects.filter(pk=attempt_id).first() if attempt_id else None
        return invoice_id, attempt

    notes = payment.get("notes")
    notes = notes if isinstance(notes, dict) else {}  # Razorpay sends [] when empty
    attempt_id = notes.get("attempt_id")
    if attempt_id and str(attempt_id).isdigit():
        attempt = PaymentAttempt.objects.filter(pk=int(attempt_id)).first()
        if attempt and str(attempt.invoice_id) == str(notes.get("invoice_id", attempt.invoice_id)):
            return attempt.invoice_id, attempt
    return None, None


def apply_payment_captured(
    *,
    gateway_payment_id: str,
    invoice_id: int,
    amount_paise: int,
    method: str = "",
    attempt: PaymentAttempt | None = None,
    order_id: str = "",
    fee_paise: int = 0,
    captured_at=None,
) -> tuple[Payment, bool]:
    """Record a captured payment and credit the invoice. Idempotent: a payment that is
    already captured is a no-op, however many times its events arrive.

    Returns (payment, applied) where `applied` is False for the no-op case.
    """
    # If we've seen this payment before, it belongs to that invoice, whatever the event says.
    known_invoice_id = (
        Payment.objects.filter(gateway_payment_id=gateway_payment_id)
        .values_list("invoice_id", flat=True)
        .first()
    )
    invoice_id = known_invoice_id or invoice_id

    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
        payment = (
            Payment.objects.select_for_update()
            .filter(gateway_payment_id=gateway_payment_id)
            .first()
        )
        if payment and payment.status == Payment.Status.CAPTURED:
            return payment, False  # already applied

        before = snapshot(invoice, INVOICE_FIELDS)
        now = captured_at or timezone.now()
        if payment is None:
            payment = Payment.objects.create(
                invoice=invoice,
                attempt=attempt,
                gateway_payment_id=gateway_payment_id,
                gateway_order_id=order_id,
                amount_paise=amount_paise,
                method=method,
                fee_paise=fee_paise,
                status=Payment.Status.CAPTURED,
                captured_at=now,
            )
        else:
            # authorized -> captured, or failed -> captured (Razorpay "late authorization":
            # money that arrives is the truth, whatever an earlier event said).
            payment.status = Payment.Status.CAPTURED
            payment.captured_at = now
            payment.amount_paise = amount_paise
            payment.method = method or payment.method
            payment.fee_paise = fee_paise or payment.fee_paise
            payment.attempt = payment.attempt or attempt
            payment.gateway_order_id = payment.gateway_order_id or order_id
            payment.save()

        LedgerEntry.objects.create(
            invoice=invoice,
            payment=payment,
            entry_type=LedgerEntry.EntryType.PAYMENT,
            credit_paise=amount_paise,
        )
        invoice.amount_paid_paise += amount_paise
        _transition_or_flag(invoice, payment)
        invoice.version += 1
        invoice.save()

        attempt = payment.attempt
        if attempt is not None:
            if amount_paise != attempt.amount_paise:
                raise_issue(
                    Kind.AMOUNT_MISMATCH,
                    gateway_payment_id,
                    invoice=invoice,
                    local_ref=attempt.reference_id,
                    expected_paise=attempt.amount_paise,
                    actual_paise=amount_paise,
                )
            PaymentAttempt.objects.filter(pk=attempt.pk).update(
                status=PaymentAttempt.Status.PAID, updated_at=timezone.now()
            )
        if invoice.amount_paid_paise > invoice.total_paise:
            raise_issue(
                Kind.OVERPAID,
                gateway_payment_id,
                invoice=invoice,
                local_ref=invoice.number or "",
                expected_paise=invoice.total_paise,
                actual_paise=invoice.amount_paid_paise,
                details={"refund_due_paise": invoice.amount_paid_paise - invoice.total_paise},
            )

        audit("payment.captured", payment)
        audit(
            "invoice.payment_applied",
            invoice,
            before=before,
            after=snapshot(invoice, INVOICE_FIELDS),
        )

        invoice_pk = invoice.pk
        transaction.on_commit(lambda: _cancel_superseded_links(invoice_pk))

    logger.info(
        "payment.captured payment=%s invoice=%s amount=%s status=%s",
        gateway_payment_id,
        invoice.pk,
        amount_paise,
        invoice.status,
    )
    return payment, True


def _transition_or_flag(invoice: Invoice, payment: Payment) -> None:
    try:
        state_machine.transition_after_payment(invoice)
    except InvalidTransition:
        # Money arrived for a cancelled/refunded invoice (e.g. a link paid just as the
        # invoice was cancelled). Record it — it's real money — keep the status, and
        # put it in front of a manager.
        raise_issue(
            Kind.PAYMENT_ON_CLOSED_INVOICE,
            payment.gateway_payment_id,
            invoice=invoice,
            local_ref=invoice.number or "",
            actual_paise=payment.amount_paise,
            details={"invoice_status": invoice.status},
        )


def _cancel_superseded_links(invoice_id: int) -> None:
    from .tasks import cancel_superseded_links

    cancel_superseded_links.delay(invoice_id)


def apply_payment_authorized(
    *, gateway_payment_id, invoice_id, amount_paise, method="", attempt=None
):
    """Note an authorization (no money moves yet). Never downgrades an existing payment."""
    with transaction.atomic():
        Invoice.objects.select_for_update().get(pk=invoice_id)
        payment, created = Payment.objects.get_or_create(
            gateway_payment_id=gateway_payment_id,
            defaults={
                "invoice_id": invoice_id,
                "attempt": attempt,
                "amount_paise": amount_paise,
                "method": method,
                "status": Payment.Status.AUTHORIZED,
            },
        )
    if not created:
        logger.info(
            "payment.stale_event_skipped payment=%s event=authorized current=%s",
            gateway_payment_id,
            payment.status,
        )
    return payment


def apply_payment_failed(
    *,
    gateway_payment_id,
    invoice_id,
    amount_paise,
    method="",
    attempt=None,
    error_code="",
    error_description="",
):
    """Record a failed payment. A payment that was already captured stays captured:
    state only moves forward, and a late `payment.failed` is logged and skipped."""
    with transaction.atomic():
        Invoice.objects.select_for_update().get(pk=invoice_id)
        payment = (
            Payment.objects.select_for_update()
            .filter(gateway_payment_id=gateway_payment_id)
            .first()
        )
        if payment is None:
            return Payment.objects.create(
                invoice_id=invoice_id,
                attempt=attempt,
                gateway_payment_id=gateway_payment_id,
                amount_paise=amount_paise,
                method=method,
                status=Payment.Status.FAILED,
                error_code=error_code,
                error_description=error_description[:255],
            )
        if payment.status == Payment.Status.AUTHORIZED:
            payment.status = Payment.Status.FAILED
            payment.error_code = error_code
            payment.error_description = error_description[:255]
            payment.save()
        else:
            logger.info(
                "payment.stale_event_skipped payment=%s event=failed current=%s",
                gateway_payment_id,
                payment.status,
            )
        return payment


# Link lifecycle: only these moves are allowed. PAID is final; a payment that lands
# right as a link expires still marks it PAID.
_ATTEMPT_FORWARD = {
    PaymentAttempt.Status.EXPIRED: [PaymentAttempt.Status.PENDING, PaymentAttempt.Status.CREATED],
    PaymentAttempt.Status.CANCELLED: [PaymentAttempt.Status.PENDING, PaymentAttempt.Status.CREATED],
}


def mark_attempt(attempt: PaymentAttempt, new_status: str) -> bool:
    """Move an attempt to expired/cancelled if it's still active. Returns True if it moved."""
    moved = PaymentAttempt.objects.filter(
        pk=attempt.pk, status__in=_ATTEMPT_FORWARD[new_status]
    ).update(status=new_status, updated_at=timezone.now())
    return bool(moved)


def cancel_open_links(invoice_id: int, *, only_exceeding_due: bool) -> int:
    """Cancel the invoice's live links at the gateway, so a customer can't pay a link
    that is no longer needed. Returns how many were cancelled."""
    invoice = Invoice.objects.get(pk=invoice_id)
    attempts = invoice.payment_attempts.filter(status=PaymentAttempt.Status.CREATED)
    if only_exceeding_due:
        attempts = attempts.filter(amount_paise__gt=invoice.amount_due_paise)
    attempts = list(attempts)
    if not attempts:
        return 0

    gateway = get_gateway()
    cancelled = 0
    for attempt in attempts:
        try:
            gateway.cancel_payment_link(attempt.gateway_link_id)
        except GatewayError as exc:
            if exc.retryable:
                raise
            # 4xx: already paid/expired/cancelled at the gateway. Its webhook will tell us which.
            logger.info(
                "payment_attempt.cancel_rejected attempt=%s link=%s error=%s",
                attempt.pk,
                attempt.gateway_link_id,
                exc,
            )
            continue
        if mark_attempt(attempt, PaymentAttempt.Status.CANCELLED):
            cancelled += 1
    return cancelled


# --- Cash ----------------------------------------------------------------------------------------


def record_cash_payment(
    invoice_id: int, *, staff, amount_paise: int | None, idempotency_key: str
) -> tuple[Payment, bool]:
    """Cash taken at the desk. Uses the same idempotent path as gateway payments: the
    Idempotency-Key becomes a deterministic payment id, so a retried request is a no-op."""
    import hashlib

    from .refunds import CASH_PREFIX

    if not idempotency_key or len(idempotency_key) > 64:
        raise BusinessValidationError("An Idempotency-Key header (max 64 chars) is required.")
    digest = hashlib.sha256(f"{invoice_id}:{idempotency_key}".encode()).hexdigest()[:32]
    payment_id = f"{CASH_PREFIX}{digest}"

    existing = Payment.objects.filter(gateway_payment_id=payment_id).first()
    if existing:
        return existing, False

    invoice = Invoice.objects.get(pk=invoice_id)
    if invoice.status not in COLLECTABLE_STATUSES:
        raise InvalidTransition(
            f"Cannot collect payment on an invoice that is {invoice.get_status_display().lower()}."
        )
    due = invoice.amount_due_paise
    amount = due if amount_paise is None else amount_paise
    if amount <= 0 or amount > due:
        raise BusinessValidationError(f"Cash amount must be between 1 and {due} paise.")

    payment, applied = apply_payment_captured(
        gateway_payment_id=payment_id, invoice_id=invoice_id, amount_paise=amount, method="cash"
    )
    if applied:
        audit("payment.cash_recorded", payment, actor=staff.user)
    return payment, applied
