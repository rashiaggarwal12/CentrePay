"""The invoice state machine. The only code allowed to change `Invoice.status`.

    draft ──issue──▶ issued ──pay(partial)──▶ partially_paid ──pay(rest)──▶ paid
      │                │                            │                        │
    cancel      cancel (nothing paid)            refund                   refund
      ▼                ▼                            ▼                        ▼
    cancelled      cancelled            partially_refunded ◀─────────── (partial)
                                                    │
                                              refund (rest)
                                                    ▼
                                                refunded

Callers pass an event; this module decides the target status or raises
InvalidTransition. The payment/refund helpers pick the event from the invoice's
amounts, so callers never decide "paid vs partially paid" themselves.
"""

from enum import StrEnum

from apps.common.exceptions import InvalidTransition

from .models import InvoiceStatus as S


class Event(StrEnum):
    ISSUE = "issue"
    CANCEL = "cancel"
    PAY_PARTIAL = "pay_partial"
    PAY_FULL = "pay_full"
    REFUND_PARTIAL = "refund_partial"
    REFUND_FULL = "refund_full"


# (current status, event) -> next status. Anything not listed is invalid.
TRANSITIONS: dict[tuple[S, Event], S] = {
    (S.DRAFT, Event.ISSUE): S.ISSUED,
    (S.DRAFT, Event.CANCEL): S.CANCELLED,
    (S.ISSUED, Event.CANCEL): S.CANCELLED,
    (S.ISSUED, Event.PAY_PARTIAL): S.PARTIALLY_PAID,
    (S.ISSUED, Event.PAY_FULL): S.PAID,
    (S.PARTIALLY_PAID, Event.PAY_PARTIAL): S.PARTIALLY_PAID,
    (S.PARTIALLY_PAID, Event.PAY_FULL): S.PAID,
    # A payment landing on an already-paid invoice (two staff collected at once,
    # or the customer overpaid) is recorded; the invoice stays paid and the
    # overpayment is flagged for a refund elsewhere.
    (S.PAID, Event.PAY_FULL): S.PAID,
    (S.PARTIALLY_PAID, Event.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PARTIALLY_PAID, Event.REFUND_FULL): S.REFUNDED,
    (S.PAID, Event.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PAID, Event.REFUND_FULL): S.REFUNDED,
    (S.PARTIALLY_REFUNDED, Event.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PARTIALLY_REFUNDED, Event.REFUND_FULL): S.REFUNDED,
}

EDITABLE_STATUSES = frozenset({S.DRAFT})
TERMINAL_STATUSES = frozenset({S.CANCELLED, S.REFUNDED})


def next_status(current: str, event: Event) -> S:
    try:
        return TRANSITIONS[(S(current), Event(event))]
    except KeyError:
        raise InvalidTransition(
            f"Cannot {Event(event).value.replace('_', ' ')} an invoice that is "
            f"{S(current).label.lower()}."
        ) from None


def can(invoice, event: Event) -> bool:
    return (S(invoice.status), Event(event)) in TRANSITIONS


def apply(invoice, event: Event) -> S:
    """Move the invoice to its next status. Does not save; the caller saves in its transaction."""
    invoice.status = next_status(invoice.status, event)
    return invoice.status


def transition_after_payment(invoice) -> S:
    """Call after adding to `amount_paid_paise`."""
    event = (
        Event.PAY_FULL if invoice.amount_paid_paise >= invoice.total_paise else Event.PAY_PARTIAL
    )
    return apply(invoice, event)


def transition_after_refund(invoice) -> S:
    """Call after adding to `amount_refunded_paise`."""
    fully = invoice.amount_refunded_paise >= invoice.amount_paid_paise
    return apply(invoice, Event.REFUND_FULL if fully else Event.REFUND_PARTIAL)
