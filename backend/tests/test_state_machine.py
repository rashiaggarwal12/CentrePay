"""Every (status, event) pair: valid ones land on the right status, the rest raise."""

import itertools
from types import SimpleNamespace

import pytest

from apps.billing import state_machine as sm
from apps.billing.models import InvoiceStatus as S
from apps.billing.state_machine import Event as E
from apps.common.exceptions import InvalidTransition

VALID = {
    (S.DRAFT, E.ISSUE): S.ISSUED,
    (S.DRAFT, E.CANCEL): S.CANCELLED,
    (S.ISSUED, E.CANCEL): S.CANCELLED,
    (S.ISSUED, E.PAY_PARTIAL): S.PARTIALLY_PAID,
    (S.ISSUED, E.PAY_FULL): S.PAID,
    (S.PARTIALLY_PAID, E.PAY_PARTIAL): S.PARTIALLY_PAID,
    (S.PARTIALLY_PAID, E.PAY_FULL): S.PAID,
    (S.PAID, E.PAY_FULL): S.PAID,
    (S.PARTIALLY_PAID, E.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PARTIALLY_PAID, E.REFUND_FULL): S.REFUNDED,
    (S.PAID, E.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PAID, E.REFUND_FULL): S.REFUNDED,
    (S.PARTIALLY_REFUNDED, E.REFUND_PARTIAL): S.PARTIALLY_REFUNDED,
    (S.PARTIALLY_REFUNDED, E.REFUND_FULL): S.REFUNDED,
}
ALL_PAIRS = list(itertools.product(S, E))
INVALID = [pair for pair in ALL_PAIRS if pair not in VALID]


def inv(status, **amounts):
    defaults = {"total_paise": 1000, "amount_paid_paise": 0, "amount_refunded_paise": 0}
    return SimpleNamespace(status=status, **{**defaults, **amounts})


def test_table_matches_spec():
    assert sm.TRANSITIONS == VALID


@pytest.mark.parametrize(("pair", "expected"), VALID.items(), ids=lambda v: str(v))
def test_valid_transitions(pair, expected):
    status, event = pair
    invoice = inv(status)
    assert sm.can(invoice, event)
    assert sm.apply(invoice, event) == expected
    assert invoice.status == expected


@pytest.mark.parametrize("pair", INVALID, ids=lambda p: f"{p[0].value}-{p[1].value}")
def test_invalid_transitions_raise(pair):
    status, event = pair
    invoice = inv(status)
    assert not sm.can(invoice, event)
    with pytest.raises(InvalidTransition):
        sm.apply(invoice, event)
    assert invoice.status == status  # unchanged


@pytest.mark.parametrize("status", [S.CANCELLED, S.REFUNDED])
def test_terminal_statuses_have_no_exits(status):
    assert not any(src == status for src, _ in sm.TRANSITIONS)


def test_spec_examples():
    with pytest.raises(InvalidTransition, match="refund"):
        sm.apply(inv(S.DRAFT), E.REFUND_FULL)
    with pytest.raises(InvalidTransition, match="cancel"):
        sm.apply(inv(S.PAID), E.CANCEL)


@pytest.mark.parametrize(
    ("status", "paid", "expected"),
    [
        (S.ISSUED, 400, S.PARTIALLY_PAID),
        (S.ISSUED, 1000, S.PAID),
        (S.ISSUED, 1500, S.PAID),  # overpaid
        (S.PARTIALLY_PAID, 999, S.PARTIALLY_PAID),
        (S.PARTIALLY_PAID, 1000, S.PAID),
        (S.PAID, 1200, S.PAID),
    ],
)
def test_transition_after_payment(status, paid, expected):
    assert sm.transition_after_payment(inv(status, amount_paid_paise=paid)) == expected


def test_payment_on_draft_or_cancelled_rejected():
    for status in (S.DRAFT, S.CANCELLED):
        with pytest.raises(InvalidTransition):
            sm.transition_after_payment(inv(status, amount_paid_paise=1000))


@pytest.mark.parametrize(
    ("status", "paid", "refunded", "expected"),
    [
        (S.PAID, 1000, 300, S.PARTIALLY_REFUNDED),
        (S.PAID, 1000, 1000, S.REFUNDED),
        (S.PARTIALLY_PAID, 400, 400, S.REFUNDED),
        (S.PARTIALLY_PAID, 400, 100, S.PARTIALLY_REFUNDED),
        (S.PARTIALLY_REFUNDED, 1000, 1000, S.REFUNDED),
    ],
)
def test_transition_after_refund(status, paid, refunded, expected):
    invoice = inv(status, amount_paid_paise=paid, amount_refunded_paise=refunded)
    assert sm.transition_after_refund(invoice) == expected
