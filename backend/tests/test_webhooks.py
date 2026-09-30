from unittest import mock

import pytest
from django.core.management import call_command

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus
from apps.ledger.models import LedgerEntry
from apps.payments.models import Payment, PaymentAttempt
from apps.reconciliation.models import ReconciliationIssue
from apps.webhooks import handlers
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import process_event
from apps.webhooks.tasks import process_webhook_event
from config.celery import app as celery_app

from . import razorpay as rzp

pytestmark = pytest.mark.django_db


def refresh(invoice) -> Invoice:
    return Invoice.objects.get(pk=invoice.pk)


def assert_ledger_matches(invoice):
    """Invariant: amount_paid == sum of captured payments == sum of ledger credits."""
    invoice = refresh(invoice)
    captured = sum(
        p.amount_paise for p in invoice.payments.all() if p.status == Payment.Status.CAPTURED
    )
    credits = sum(e.credit_paise for e in LedgerEntry.objects.filter(invoice=invoice))
    assert invoice.amount_paid_paise == captured == credits


# --- Receiving ---------------------------------------------------------------------------------


def test_valid_webhook_marks_invoice_paid(issued_invoice, collect, deliver):
    attempt = collect(issued_invoice)
    resp = deliver(rzp.captured_event(attempt))
    assert resp.status_code == 200

    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_paid_paise == 236_000
    payment = Payment.objects.get()
    assert payment.method == "upi"
    assert payment.attempt == attempt
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PAID
    assert WebhookEvent.objects.get().processed_at is not None
    assert_ledger_matches(invoice)


@pytest.mark.parametrize("signature", ["", "deadbeef", "WRONG_SECRET"])
def test_invalid_signature_rejected(issued_invoice, collect, deliver, signature):
    """Edge case 3: bad or missing signature -> 400, nothing stored."""
    attempt = collect(issued_invoice)
    payload = rzp.captured_event(attempt)
    if signature == "WRONG_SECRET":
        resp = deliver(payload, secret="not-the-secret")
    else:
        resp = deliver(payload, signature=signature)
    assert resp.status_code == 400
    assert WebhookEvent.objects.count() == 0
    assert Payment.objects.count() == 0


def test_signature_is_checked_on_raw_bytes(issued_invoice, collect, client):
    """Re-serialising the JSON (e.g. different spacing) must not validate."""
    attempt = collect(issued_invoice)
    payload = rzp.captured_event(attempt)
    signature = rzp.sign(rzp.encode(payload), "test-webhook-secret")
    import json

    reformatted = json.dumps(payload, indent=2).encode()
    resp = client.post(
        "/webhooks/razorpay/",
        data=reformatted,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=signature,
    )
    assert resp.status_code == 400


def test_webhook_rejects_get(client):
    assert client.get("/webhooks/razorpay/").status_code == 405


def test_malformed_but_signed_payload_rejected(client):
    body = b'{"not": "an event"}'
    resp = client.post(
        "/webhooks/razorpay/",
        data=body,
        content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=rzp.sign(body, "test-webhook-secret"),
    )
    assert resp.status_code == 400


def test_duplicate_webhook_creates_one_payment(issued_invoice, collect, deliver):
    """Edge case 1: the same event delivered 3 times is applied once."""
    attempt = collect(issued_invoice)
    payload = rzp.captured_event(attempt)
    for _ in range(3):
        assert deliver(payload, event_id="evt_same").status_code == 200

    assert WebhookEvent.objects.count() == 1
    assert Payment.objects.count() == 1
    assert LedgerEntry.objects.count() == 1
    assert refresh(issued_invoice).amount_paid_paise == 236_000


def test_captured_and_link_paid_for_same_payment_apply_once(issued_invoice, collect, deliver):
    """Two different events carry the same payment; whichever lands first applies it."""
    attempt = collect(issued_invoice)
    payment_id = rzp.next_id("pay")
    deliver(rzp.link_paid_event(attempt, payment_id=payment_id))
    deliver(rzp.captured_event(attempt, payment_id=payment_id))

    assert WebhookEvent.objects.count() == 2
    assert Payment.objects.count() == 1
    assert refresh(issued_invoice).amount_paid_paise == 236_000
    assert_ledger_matches(issued_invoice)


def test_link_paid_alone_applies_payment_without_notes(issued_invoice, collect, deliver):
    """If payment.captured can't be matched (no notes), payment_link.paid still applies it."""
    attempt = collect(issued_invoice)
    payment_id = rzp.next_id("pay")
    bare = rzp.event(
        "payment.captured",
        payment=rzp.payment_entity(payment_id=payment_id, amount=attempt.amount_paise),
    )
    deliver(bare)
    assert Payment.objects.count() == 0  # unmatched: waits for the link event

    deliver(rzp.link_paid_event(attempt, payment_id=payment_id))
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_unknown_event_is_stored_and_ignored(deliver):
    resp = deliver(rzp.event("order.paid", order={"id": "order_x"}))
    assert resp.status_code == 200
    event = WebhookEvent.objects.get()
    assert event.processed_at is not None


# --- Payment edge cases ------------------------------------------------------------------------


def test_partial_then_full_payment(issued_invoice, collect, deliver):
    """Edge case 10: partially_paid until the sum reaches the total."""
    first = collect(issued_invoice, amount_paise=100_000)
    deliver(rzp.captured_event(first))
    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    assert invoice.amount_due_paise == 136_000

    rest = collect(invoice)
    assert rest.amount_paise == 136_000
    deliver(rzp.captured_event(rest))
    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_due_paise == 0
    assert_ledger_matches(invoice)


def test_stale_event_does_not_downgrade(issued_invoice, collect, deliver):
    """Edge case 5: payment.failed arriving after payment.captured is skipped."""
    attempt = collect(issued_invoice)
    payment_id = rzp.next_id("pay")
    deliver(rzp.captured_event(attempt, payment_id=payment_id))
    failed = rzp.event(
        "payment.failed",
        payment=rzp.payment_entity(
            payment_id=payment_id,
            amount=attempt.amount_paise,
            status="failed",
            notes=rzp.notes_for(attempt),
            error_code="BAD_REQUEST_ERROR",
        ),
    )
    deliver(failed)
    assert Payment.objects.get().status == Payment.Status.CAPTURED
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_failed_then_captured_late_authorization(issued_invoice, collect, deliver):
    """A payment that failed and later got captured (late auth) counts as money received."""
    attempt = collect(issued_invoice)
    payment_id = rzp.next_id("pay")
    notes = rzp.notes_for(attempt)
    deliver(
        rzp.event(
            "payment.failed",
            payment=rzp.payment_entity(
                payment_id=payment_id, amount=attempt.amount_paise, status="failed", notes=notes
            ),
        )
    )
    assert Payment.objects.get().status == Payment.Status.FAILED
    assert refresh(issued_invoice).status == InvoiceStatus.ISSUED

    deliver(rzp.captured_event(attempt, payment_id=payment_id))
    assert Payment.objects.get().status == Payment.Status.CAPTURED
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_authorized_then_captured(issued_invoice, collect, deliver):
    attempt = collect(issued_invoice)
    payment_id = rzp.next_id("pay")
    deliver(
        rzp.event(
            "payment.authorized",
            payment=rzp.payment_entity(
                payment_id=payment_id,
                amount=attempt.amount_paise,
                status="authorized",
                notes=rzp.notes_for(attempt),
            ),
        )
    )
    assert Payment.objects.get().status == Payment.Status.AUTHORIZED
    assert refresh(issued_invoice).amount_paid_paise == 0  # no money yet

    deliver(rzp.captured_event(attempt, payment_id=payment_id))
    assert refresh(issued_invoice).status == InvoiceStatus.PAID
    assert LedgerEntry.objects.count() == 1


def test_overpayment_flagged(issued_invoice, collect, desk, deliver):
    """Edge case 9 (via 8): two links for the same invoice both get paid."""
    first = collect(issued_invoice, amount_paise=236_000)
    # Force a second live link (e.g. created before the first was reused).
    second = collect(issued_invoice, amount_paise=236_000 - 100)

    deliver(rzp.captured_event(first))
    deliver(rzp.captured_event(second))

    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_paid_paise == 236_000 * 2 - 100
    issue = ReconciliationIssue.objects.get(kind="overpaid")
    assert issue.invoice_id == invoice.pk
    assert issue.details["refund_due_paise"] == 236_000 - 100
    assert_ledger_matches(invoice)


def test_amount_mismatch_flagged(issued_invoice, collect, deliver):
    """Edge case 13: the gateway's amount wins, and the mismatch is raised."""
    attempt = collect(issued_invoice)
    deliver(rzp.captured_event(attempt, amount=200_000))

    invoice = refresh(issued_invoice)
    assert invoice.amount_paid_paise == 200_000
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    issue = ReconciliationIssue.objects.get(kind="amount_mismatch")
    assert (issue.expected_paise, issue.actual_paise) == (236_000, 200_000)


def test_payment_on_cancelled_invoice_is_recorded_and_flagged(
    issued_invoice, collect, manager, deliver
):
    attempt = collect(issued_invoice)
    # Cancel without running on_commit, i.e. the link-cancel call hasn't reached Razorpay yet.
    billing.cancel_invoice(issued_invoice.pk, staff=manager, reason="left")
    deliver(rzp.captured_event(attempt))

    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.CANCELLED
    assert invoice.amount_paid_paise == 236_000  # the money is real; it's recorded
    assert ReconciliationIssue.objects.filter(kind="payment_on_closed_invoice").exists()
    assert WebhookEvent.objects.get().processed_at is not None  # not stuck retrying


def test_link_we_did_not_create_raises_issue(deliver):
    payload = rzp.event(
        "payment_link.paid",
        payment_link=rzp.link_entity(link_id="plink_foreign", reference_id="manual-1", amount=5000),
        payment=rzp.payment_entity(amount=5000),
    )
    deliver(payload)
    assert Payment.objects.count() == 0
    assert ReconciliationIssue.objects.get().kind == "unmatched_payment"


def test_full_payment_cancels_other_live_links(
    issued_invoice, collect, deliver, mock_link_creation
):
    partial = collect(issued_invoice, amount_paise=100_000)
    full = collect(issued_invoice)  # a different amount, so a separate live link
    deliver(rzp.captured_event(full))

    partial.refresh_from_db()
    assert partial.status == PaymentAttempt.Status.CANCELLED
    cancelled_urls = [
        c.request.url for c in mock_link_creation.calls if c.request.url.endswith("/cancel")
    ]
    assert cancelled_urls == [
        f"https://api.razorpay.com/v1/payment_links/{partial.gateway_link_id}/cancel"
    ]


# --- Link lifecycle ----------------------------------------------------------------------------


def _link_event(event_type, attempt, status):
    return rzp.event(
        event_type,
        payment_link=rzp.link_entity(
            link_id=attempt.gateway_link_id,
            reference_id=attempt.reference_id,
            amount=attempt.amount_paise,
            status=status,
        ),
    )


def test_link_expired(issued_invoice, collect, deliver):
    attempt = collect(issued_invoice)
    deliver(_link_event("payment_link.expired", attempt, "expired"))
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.EXPIRED


def test_paid_link_is_not_downgraded_by_late_expiry(issued_invoice, collect, deliver):
    attempt = collect(issued_invoice)
    deliver(rzp.captured_event(attempt))
    deliver(_link_event("payment_link.expired", attempt, "expired"))
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PAID


def test_expired_link_then_paid_still_counts(issued_invoice, collect, deliver):
    attempt = collect(issued_invoice)
    deliver(_link_event("payment_link.expired", attempt, "expired"))
    deliver(rzp.link_paid_event(attempt))
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.PAID
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


# --- Failure and replay ------------------------------------------------------------------------


def test_processing_failure_is_retried(issued_invoice, collect, deliver):
    """Edge case 6: the raw event is stored first, so a crash in processing loses nothing.
    The task retries; here the first two attempts fail and the third succeeds."""
    attempt = collect(issued_invoice)
    real = handlers.HANDLERS["payment.captured"]
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("db went away")
        return real(payload)

    # The worker is down when the webhook arrives: we still store it and answer 200.
    with mock.patch("apps.webhooks.views.process_webhook_event.delay"):
        resp = deliver(rzp.captured_event(attempt))
    assert resp.status_code == 200
    event = WebhookEvent.objects.get()
    assert event.processed_at is None

    # The worker picks it up; the task retries through two failures. (Eager mode must
    # not propagate, or Celery raises Retry instead of running the retry inline.)
    celery_app.conf.update(task_eager_propagates=False, CELERY_TASK_EAGER_PROPAGATES=False)
    try:
        with mock.patch.dict(handlers.HANDLERS, {"payment.captured": flaky}):
            result = process_webhook_event.apply(args=(event.pk,))
    finally:
        celery_app.conf.update(task_eager_propagates=True, CELERY_TASK_EAGER_PROPAGATES=True)

    assert result.successful(), result.traceback
    event.refresh_from_db()
    assert event.processed_at is not None
    assert event.attempts == 3  # 2 failures recorded + 1 success
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_failed_processing_rolls_back_partial_work(issued_invoice, collect):
    """If a handler dies midway, neither its changes nor processed_at are committed."""
    attempt = collect(issued_invoice)
    event = WebhookEvent.objects.create(
        gateway_event_id="evt_boom",
        event_type="payment.captured",
        raw_payload=rzp.captured_event(attempt),
    )
    with (
        mock.patch("apps.payments.services.audit", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        process_event(event.pk)

    event.refresh_from_db()
    assert event.processed_at is None
    assert Payment.objects.count() == 0
    assert LedgerEntry.objects.count() == 0
    assert refresh(issued_invoice).amount_paid_paise == 0


def test_replay_command_processes_stuck_events(issued_invoice, collect):
    attempt = collect(issued_invoice)
    stuck = WebhookEvent.objects.create(
        gateway_event_id="evt_stuck",
        event_type="payment.captured",
        raw_payload=rzp.captured_event(attempt),
    )
    call_command("replay_webhooks", "--unprocessed", "--sync")
    stuck.refresh_from_db()
    assert stuck.processed_at is not None
    assert refresh(issued_invoice).status == InvoiceStatus.PAID

    # Forcing a replay of an already-processed event is harmless (idempotent handlers).
    call_command("replay_webhooks", "--id", str(stuck.pk), "--force", "--sync")
    assert Payment.objects.count() == 1
    assert refresh(issued_invoice).amount_paid_paise == 236_000


def test_ledger_is_append_only(issued_invoice, collect, deliver):
    deliver(rzp.captured_event(collect(issued_invoice)))
    entry = LedgerEntry.objects.get()
    entry.credit_paise = 1
    with pytest.raises(RuntimeError):
        entry.save()
    with pytest.raises(RuntimeError):
        entry.delete()
