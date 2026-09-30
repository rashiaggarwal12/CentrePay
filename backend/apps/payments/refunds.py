"""Refunds: request -> manager approval -> gateway -> webhook.

Money only moves (ledger debit, invoice.amount_refunded) in `apply_refund_processed`,
which is idempotent and reachable from both the gateway's API response and the
`refund.processed` webhook, whichever arrives first.
"""

import logging
import uuid

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import audit, snapshot
from apps.billing import state_machine
from apps.billing.models import Invoice, InvoiceStatus
from apps.billing.services import INVOICE_FIELDS
from apps.common.exceptions import (
    BusinessValidationError,
    DependencyNotReady,
    DomainError,
    InvalidTransition,
    ManagerRequired,
)
from apps.ledger.models import LedgerEntry
from apps.reconciliation.models import ReconciliationIssue
from apps.reconciliation.services import Kind, raise_issue

from .gateway import GatewayError, get_gateway
from .models import Payment, Refund

logger = logging.getLogger(__name__)

CASH_PREFIX = "cash_"


class RefundExceedsPaid(BusinessValidationError):
    default_code = "REFUND_EXCEEDS_PAID"
    default_detail = "The refund is larger than the amount still refundable on this payment."


class RefundNotPending(DomainError):
    default_code = "REFUND_NOT_PENDING"
    default_detail = "Only a requested refund can be approved or rejected."


def is_cash(payment: Payment) -> bool:
    return payment.gateway_payment_id.startswith(CASH_PREFIX)


def refundable_paise(payment: Payment) -> int:
    reserved = (
        payment.refunds.filter(status__in=Refund.RESERVING_STATUSES).aggregate(
            total=Sum("amount_paise")
        )["total"]
        or 0
    )
    return payment.amount_paise - reserved


# --- Request / decide ---------------------------------------------------------------------------


@transaction.atomic
def request_refund(payment_id: int, *, staff, amount_paise: int, reason: str) -> Refund:
    invoice_id = Payment.objects.values_list("invoice_id", flat=True).get(pk=payment_id)
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)  # serialises refund requests
    payment = Payment.objects.get(pk=payment_id)

    if payment.status != Payment.Status.CAPTURED:
        raise BusinessValidationError("Only captured payments can be refunded.")
    if amount_paise <= 0:
        raise BusinessValidationError("Refund amount must be positive.")
    available = refundable_paise(payment)
    if amount_paise > available:
        # Edge case 11: stopped here, before the gateway is ever called.
        raise RefundExceedsPaid(
            f"At most {available} paise can still be refunded on this payment "
            f"(paid {payment.amount_paise}, already refunded or pending "
            f"{payment.amount_paise - available})."
        )

    refund = Refund.objects.create(
        payment=payment,
        invoice=invoice,
        receipt=f"rf_{uuid.uuid4().hex[:24]}",
        amount_paise=amount_paise,
        reason=reason,
        requested_by=staff,
    )
    audit("refund.requested", refund, actor=staff.user)
    return refund


def _lock_refund(refund_id: int) -> Refund:
    invoice_id = Refund.objects.values_list("invoice_id", flat=True).get(pk=refund_id)
    Invoice.objects.select_for_update().get(pk=invoice_id)
    return Refund.objects.select_for_update().select_related("payment").get(pk=refund_id)


@transaction.atomic
def approve_refund(refund_id: int, *, staff) -> Refund:
    if not staff.is_manager:
        raise ManagerRequired("Only a manager can approve refunds.")
    refund = _lock_refund(refund_id)
    if refund.status != Refund.Status.REQUESTED:
        raise RefundNotPending()
    before = snapshot(refund)
    refund.status = Refund.Status.APPROVED
    refund.approved_by = staff
    refund.decided_at = timezone.now()
    refund.save()
    audit("refund.approved", refund, actor=staff.user, before=before)

    if is_cash(refund.payment):
        # Cash goes back over the counter: nothing to send to a gateway.
        _apply_processed(refund, amount_paise=refund.amount_paise)
    else:
        from .tasks import process_refund

        refund_pk = refund.pk
        transaction.on_commit(lambda: process_refund.delay(refund_pk))
    return refund


@transaction.atomic
def reject_refund(refund_id: int, *, staff, note: str) -> Refund:
    if not staff.is_manager:
        raise ManagerRequired("Only a manager can reject refunds.")
    refund = _lock_refund(refund_id)
    if refund.status != Refund.Status.REQUESTED:
        raise RefundNotPending()
    before = snapshot(refund)
    refund.status = Refund.Status.REJECTED
    refund.approved_by = staff
    refund.decision_note = note
    refund.decided_at = timezone.now()
    refund.save()
    audit("refund.rejected", refund, actor=staff.user, before=before)
    return refund


# --- Talking to the gateway ---------------------------------------------------------------------


def submit_to_gateway(refund_id: int) -> str:
    """Send an approved refund to the gateway. Safe to call repeatedly.

    The refund's `receipt` travels with it, so if a previous call reached the gateway but
    we never saw the response, we find that refund instead of refunding twice.
    """
    claimed = Refund.objects.filter(pk=refund_id, status=Refund.Status.APPROVED).update(
        status=Refund.Status.PROCESSING, updated_at=timezone.now()
    )
    refund = Refund.objects.select_related("payment", "invoice").get(pk=refund_id)
    if not claimed and not (
        refund.status == Refund.Status.PROCESSING and refund.gateway_refund_id is None
    ):
        return f"skipped ({refund.status})"

    gateway = get_gateway()
    payment_gid = refund.payment.gateway_payment_id
    try:
        result = next(
            (r for r in gateway.list_refunds(payment_gid) if r.get("receipt") == refund.receipt),
            None,
        ) or gateway.create_refund(
            payment_gid,
            amount_paise=refund.amount_paise,
            receipt=refund.receipt,
            notes={"refund_id": str(refund.pk), "invoice_id": str(refund.invoice_id)},
        )
    except GatewayError as exc:
        if exc.retryable:
            raise  # the task retries; status stays PROCESSING with no gateway id
        Refund.objects.filter(pk=refund.pk).update(
            status=Refund.Status.FAILED, last_error=str(exc)[:1000], updated_at=timezone.now()
        )
        logger.warning("refund.gateway_rejected refund=%s error=%s", refund.pk, exc)
        return "failed"

    Refund.objects.filter(pk=refund.pk, gateway_refund_id__isnull=True).update(
        gateway_refund_id=result["id"]
    )
    if result.get("status") == "processed":
        apply_refund_processed(
            gateway_refund_id=result["id"],
            payment_gateway_id=payment_gid,
            amount_paise=int(result["amount"]),
            notes=result.get("notes"),
        )
    elif result.get("status") == "failed":
        apply_refund_failed(gateway_refund_id=result["id"], notes=result.get("notes"))
    return result.get("status", "unknown")


# --- Applying gateway outcomes ------------------------------------------------------------------


def _find_refund(gateway_refund_id: str, notes) -> Refund | None:
    refund = Refund.objects.filter(gateway_refund_id=gateway_refund_id).first()
    if refund is None and isinstance(notes, dict) and str(notes.get("refund_id", "")).isdigit():
        refund = Refund.objects.filter(pk=int(notes["refund_id"])).first()
    return refund


def apply_refund_processed(
    *, gateway_refund_id: str, payment_gateway_id: str, amount_paise: int, notes=None
) -> tuple[Refund, bool]:
    """Idempotent. Returns (refund, applied). Raises DependencyNotReady if we don't know
    the payment yet (edge case 4: the refund event overtook the payment event)."""
    payment = Payment.objects.filter(gateway_payment_id=payment_gateway_id).first()
    if payment is None:
        raise DependencyNotReady(
            f"refund {gateway_refund_id} for unknown payment {payment_gateway_id}"
        )

    with transaction.atomic():
        Invoice.objects.select_for_update().get(pk=payment.invoice_id)
        refund = _find_refund(gateway_refund_id, notes)
        if refund is None:
            # Refunded outside CentrePay (e.g. from the gateway dashboard). The money has
            # left, so record it, and let a human know it bypassed approval.
            refund = Refund.objects.create(
                payment=payment,
                invoice_id=payment.invoice_id,
                gateway_refund_id=gateway_refund_id,
                receipt=f"ext_{gateway_refund_id}"[:40],
                amount_paise=amount_paise,
                reason="Refunded outside CentrePay",
                status=Refund.Status.PROCESSING,
            )
            raise_issue(
                Kind.STATUS_MISMATCH,
                gateway_refund_id,
                invoice=payment.invoice,
                local_ref=payment.gateway_payment_id,
                actual_paise=amount_paise,
                details={"reason": "refund created outside CentrePay, without approval"},
            )
        refund = Refund.objects.select_for_update().get(pk=refund.pk)
        if refund.status == Refund.Status.PROCESSED:
            return refund, False
        _apply_processed(refund, amount_paise=amount_paise, gateway_refund_id=gateway_refund_id)
    return refund, True


def _apply_processed(refund: Refund, *, amount_paise: int, gateway_refund_id: str | None = None):
    """Caller holds the invoice lock and the refund lock, inside a transaction."""
    invoice = Invoice.objects.select_for_update().get(pk=refund.invoice_id)
    before = snapshot(invoice, INVOICE_FIELDS)

    if amount_paise != refund.amount_paise:
        raise_issue(
            Kind.AMOUNT_MISMATCH,
            gateway_refund_id or refund.receipt,
            invoice=invoice,
            local_ref=refund.receipt,
            expected_paise=refund.amount_paise,
            actual_paise=amount_paise,
        )
    refund.status = Refund.Status.PROCESSED
    refund.gateway_refund_id = refund.gateway_refund_id or gateway_refund_id
    refund.amount_paise = amount_paise  # the gateway's figure is what actually left
    refund.processed_at = timezone.now()
    refund.last_error = ""
    refund.save()

    LedgerEntry.objects.create(
        invoice=invoice,
        payment=refund.payment,
        refund=refund,
        entry_type=LedgerEntry.EntryType.REFUND,
        debit_paise=amount_paise,
    )
    invoice.amount_refunded_paise += amount_paise
    _transition_after_refund(invoice)
    invoice.version += 1
    invoice.save()

    audit("refund.processed", refund)
    audit("invoice.refund_applied", invoice, before=before, after=snapshot(invoice, INVOICE_FIELDS))
    logger.info(
        "refund.processed refund=%s invoice=%s amount=%s status=%s",
        refund.pk,
        invoice.pk,
        amount_paise,
        invoice.status,
    )


def _transition_after_refund(invoice: Invoice) -> None:
    net_paid = invoice.amount_paid_paise - invoice.amount_refunded_paise
    if invoice.status == InvoiceStatus.PAID and net_paid >= invoice.total_paise:
        # Refunding an overpayment: the invoice is still fully paid.
        if net_paid == invoice.total_paise:
            ReconciliationIssue.objects.filter(
                invoice=invoice,
                kind=Kind.OVERPAID,
                status=ReconciliationIssue.Status.OPEN,
            ).update(
                status=ReconciliationIssue.Status.RESOLVED,
                resolution_note="Excess refunded to the customer.",
                resolved_at=timezone.now(),
            )
        return
    try:
        state_machine.transition_after_refund(invoice)
    except InvalidTransition:
        # e.g. refunding money that arrived on a cancelled invoice: status stays cancelled.
        logger.info("refund.no_transition invoice=%s status=%s", invoice.pk, invoice.status)


def apply_refund_failed(*, gateway_refund_id: str, notes=None, error: str = "") -> Refund | None:
    refund = _find_refund(gateway_refund_id, notes)
    if refund is None:
        return None
    moved = Refund.objects.filter(
        pk=refund.pk, status__in=[Refund.Status.APPROVED, Refund.Status.PROCESSING]
    ).update(
        status=Refund.Status.FAILED,
        gateway_refund_id=refund.gateway_refund_id or gateway_refund_id,
        last_error=error[:1000],
        updated_at=timezone.now(),
    )
    if not moved:
        logger.info("refund.stale_failed_event refund=%s status=%s", refund.pk, refund.status)
    refund.refresh_from_db()
    return refund
