"""One handler per Razorpay event type. Each handler must be idempotent: running it
twice for the same payload must leave the same end state.

Payments made through a payment link produce two events carrying the same payment:
`payment.captured` and `payment_link.paid`. Both route to the same idempotent service
call keyed by the payment id, so whichever arrives first applies it and the other is
a no-op.
"""

import logging
from datetime import UTC, datetime

from apps.payments import services as payments
from apps.payments.models import Payment, PaymentAttempt
from apps.reconciliation.services import Kind, raise_issue

logger = logging.getLogger(__name__)


def _entity(payload: dict, name: str) -> dict | None:
    return (payload.get("payload", {}).get(name) or {}).get("entity")


def _notes(entity: dict) -> dict:
    notes = entity.get("notes")
    return notes if isinstance(notes, dict) else {}  # Razorpay sends [] when empty


def _ts(unix: int | None):
    return datetime.fromtimestamp(unix, tz=UTC) if unix else None


def _resolve_payment_target(payment: dict) -> tuple[int | None, PaymentAttempt | None]:
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

    notes = _notes(payment)
    attempt_id = notes.get("attempt_id")
    if attempt_id and str(attempt_id).isdigit():
        attempt = PaymentAttempt.objects.filter(pk=int(attempt_id)).first()
        if attempt and str(attempt.invoice_id) == str(notes.get("invoice_id", attempt.invoice_id)):
            return attempt.invoice_id, attempt
    return None, None


def _payment_kwargs(payment: dict) -> dict:
    return {
        "gateway_payment_id": payment["id"],
        "amount_paise": int(payment["amount"]),
        "method": payment.get("method") or "",
    }


def _find_attempt_for_link(link: dict) -> PaymentAttempt | None:
    return (
        PaymentAttempt.objects.filter(gateway_link_id=link["id"]).first()
        or PaymentAttempt.objects.filter(reference_id=link.get("reference_id") or "-").first()
    )


# --- payment.* ---------------------------------------------------------------------------------


def handle_payment_captured(payload: dict) -> None:
    payment = _entity(payload, "payment")
    invoice_id, attempt = _resolve_payment_target(payment)
    if invoice_id is None:
        # Can't tell which invoice yet. For link payments, `payment_link.paid` carries
        # the link id and will apply it; anything left over is caught by reconciliation.
        logger.info("webhook.payment_unmatched payment=%s (awaiting link event)", payment["id"])
        return
    payments.apply_payment_captured(
        **_payment_kwargs(payment),
        invoice_id=invoice_id,
        attempt=attempt,
        order_id=payment.get("order_id") or "",
        fee_paise=int(payment.get("fee") or 0),
        captured_at=_ts(payload.get("created_at")),
    )


def handle_payment_authorized(payload: dict) -> None:
    payment = _entity(payload, "payment")
    invoice_id, attempt = _resolve_payment_target(payment)
    if invoice_id is None:
        return
    payments.apply_payment_authorized(
        **_payment_kwargs(payment), invoice_id=invoice_id, attempt=attempt
    )


def handle_payment_failed(payload: dict) -> None:
    payment = _entity(payload, "payment")
    invoice_id, attempt = _resolve_payment_target(payment)
    if invoice_id is None:
        return
    payments.apply_payment_failed(
        **_payment_kwargs(payment),
        invoice_id=invoice_id,
        attempt=attempt,
        error_code=payment.get("error_code") or "",
        error_description=payment.get("error_description") or "",
    )


# --- payment_link.* ----------------------------------------------------------------------------


def handle_payment_link_paid(payload: dict) -> None:
    link = _entity(payload, "payment_link")
    payment = _entity(payload, "payment")
    attempt = _find_attempt_for_link(link)
    if attempt is None:
        # A link we didn't create (e.g. made by hand in the Razorpay dashboard).
        raise_issue(
            Kind.UNMATCHED_PAYMENT,
            payment["id"] if payment else link["id"],
            details={"payment_link_id": link["id"], "amount_paid": link.get("amount_paid")},
        )
        return
    if payment is None:
        return
    payments.apply_payment_captured(
        **_payment_kwargs(payment),
        invoice_id=attempt.invoice_id,
        attempt=attempt,
        order_id=payment.get("order_id") or link.get("order_id") or "",
        fee_paise=int(payment.get("fee") or 0),
        captured_at=_ts(payload.get("created_at")),
    )


def handle_payment_link_expired(payload: dict) -> None:
    attempt = _find_attempt_for_link(_entity(payload, "payment_link"))
    if attempt:
        payments.mark_attempt(attempt, PaymentAttempt.Status.EXPIRED)


def handle_payment_link_cancelled(payload: dict) -> None:
    attempt = _find_attempt_for_link(_entity(payload, "payment_link"))
    if attempt:
        payments.mark_attempt(attempt, PaymentAttempt.Status.CANCELLED)


HANDLERS = {
    "payment.authorized": handle_payment_authorized,
    "payment.captured": handle_payment_captured,
    "payment.failed": handle_payment_failed,
    "payment_link.paid": handle_payment_link_paid,
    # accept_partial is off, but if it's ever turned on the payment is still real money.
    "payment_link.partially_paid": handle_payment_link_paid,
    "payment_link.expired": handle_payment_link_expired,
    "payment_link.cancelled": handle_payment_link_cancelled,
}
