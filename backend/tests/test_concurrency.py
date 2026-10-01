"""Real concurrency: threads with their own DB connections, against Postgres.

SQLite has no row locks, so these are skipped there. CI runs them on Postgres;
locally: `python scripts/local_postgres.py` and DATABASE_URL in .env (see README).
"""

import threading
import uuid

import pytest
from django.db import connection, connections

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus
from apps.common.exceptions import RequestInProgress
from apps.ledger.models import LedgerEntry
from apps.payments import services as payments
from apps.payments.models import Payment, PaymentAttempt
from apps.webhooks.models import WebhookEvent

from . import razorpay as rzp

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="needs Postgres row locks (SQLite has none)"
    ),
]


def run_concurrently(fn, n: int) -> list:
    """Start n threads that call fn(i) at the same instant. Returns results/exceptions."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i):
        try:
            barrier.wait()
            results[i] = fn(i)
        except Exception as exc:  # collected and asserted on by the test
            results[i] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_concurrent_duplicate_webhooks(issued_invoice, collect, client):
    """Edge case 2: identical deliveries arriving together -> one event, one payment.
    The unique constraint decides, not an application-level 'already seen?' check."""
    from django.test import Client

    attempt = collect(issued_invoice)
    body = rzp.encode(rzp.captured_event(attempt))
    signature = rzp.sign(body, "test-webhook-secret")

    def deliver(_):
        return (
            Client()
            .post(
                "/webhooks/razorpay/",
                data=body,
                content_type="application/json",
                HTTP_X_RAZORPAY_SIGNATURE=signature,
                HTTP_X_RAZORPAY_EVENT_ID="evt_concurrent",
            )
            .status_code
        )

    results = run_concurrently(deliver, 8)
    assert results == [200] * 8
    assert WebhookEvent.objects.count() == 1
    assert Payment.objects.count() == 1
    assert Invoice.objects.get(pk=issued_invoice.pk).amount_paid_paise == 236_000


def test_concurrent_payments_locked(issued_invoice, collect):
    """Edge case 8: two payments for the same invoice applied at the same moment.
    Without the row lock, both would read amount_paid=0 and one update would be lost."""
    attempts = [collect(issued_invoice, amount_paise=118_000) for _ in range(1)]
    attempts.append(
        PaymentAttempt.objects.create(
            invoice=issued_invoice,
            amount_paise=118_000,
            idempotency_key=uuid.uuid4().hex,
            reference_id=f"cp_{uuid.uuid4().hex[:24]}",
            status=PaymentAttempt.Status.CREATED,
        )
    )

    def pay(i):
        payments.apply_payment_captured(
            gateway_payment_id=f"pay_concurrent_{i}",
            invoice_id=issued_invoice.pk,
            amount_paise=118_000,
            method="upi",
            attempt=attempts[i],
        )

    results = run_concurrently(pay, 2)
    assert results == [None, None]

    invoice = Invoice.objects.get(pk=issued_invoice.pk)
    assert invoice.amount_paid_paise == 236_000
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.version == issued_invoice.version + 2
    assert LedgerEntry.objects.filter(invoice=invoice).count() == 2


def test_concurrent_same_payment_applied_once(issued_invoice, collect):
    """payment.captured and payment_link.paid for the same payment processed at once."""
    attempt = collect(issued_invoice)

    def pay(_):
        _, applied = payments.apply_payment_captured(
            gateway_payment_id="pay_same",
            invoice_id=issued_invoice.pk,
            amount_paise=236_000,
            attempt=attempt,
        )
        return applied

    results = run_concurrently(pay, 6)
    assert sorted(results) == [False] * 5 + [True]
    assert Invoice.objects.get(pk=issued_invoice.pk).amount_paid_paise == 236_000
    assert LedgerEntry.objects.count() == 1


def test_concurrent_collect_same_key_creates_one_link(issued_invoice, desk, mock_link_creation):
    """Edge case 12 under real concurrency: a double tap sends two requests at once."""

    def tap(_):
        attempt, _ = payments.collect(issued_invoice.pk, staff=desk, idempotency_key="double-tap")
        return attempt.pk

    results = run_concurrently(tap, 5)
    # Losers either see the finished attempt or are told to retry; none creates a link.
    pks = [r for r in results if isinstance(r, int)]
    others = [r for r in results if not isinstance(r, int)]
    assert pks and len(set(pks)) == 1, results
    assert all(isinstance(r, RequestInProgress) for r in others), results
    assert PaymentAttempt.objects.get().status == PaymentAttempt.Status.CREATED
    creations = [
        c
        for c in mock_link_creation.calls
        if c.request.method == "POST" and "cancel" not in c.request.url
    ]
    assert len(creations) == 1


def test_concurrent_issue_gets_distinct_sequential_numbers(make_draft, desk):
    drafts = [make_draft() for _ in range(6)]

    def issue(i):
        return billing.issue_invoice(drafts[i].pk, staff=desk).number

    numbers = run_concurrently(issue, 6)
    assert all(isinstance(n, str) for n in numbers), numbers
    seqs = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
    assert seqs == list(range(1, 7))


def test_ledger_trigger_blocks_bulk_update_and_delete(issued_invoice):
    """The model guard can be bypassed with queryset.update(); the DB trigger can't."""
    from django.db import DatabaseError, transaction

    payments.apply_payment_captured(
        gateway_payment_id="pay_trigger", invoice_id=issued_invoice.pk, amount_paise=1000
    )
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        LedgerEntry.objects.update(credit_paise=1)
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        LedgerEntry.objects.all()._raw_delete(LedgerEntry.objects.db)
    assert LedgerEntry.objects.get().credit_paise == 1000
