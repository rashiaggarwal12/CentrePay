import uuid
from unittest import mock

import pytest
import requests
import responses

from apps.audit.models import AuditLog
from apps.billing.models import Invoice, InvoiceStatus
from apps.ledger.models import LedgerEntry
from apps.payments import refunds
from apps.payments import services as payments
from apps.payments.models import Payment, Refund
from apps.reconciliation.models import ReconciliationIssue
from apps.sandbox.models import FakeRefund
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import process_event
from apps.webhooks.tasks import process_webhook_event
from config.celery import app as celery_app

from . import razorpay as rzp
from .conftest import RZP
from .test_api import assert_error

pytestmark = pytest.mark.django_db


def refresh(invoice):
    return Invoice.objects.get(pk=invoice.pk)


@pytest.fixture
def paid(issued_invoice, sandbox_collect, customer_pays):
    """A ₹2,360 invoice paid by UPI through the sandbox."""
    customer_pays(sandbox_collect(issued_invoice))
    return Payment.objects.get(invoice=issued_invoice)


def approve(refund, manager, run_on_commit):
    run_on_commit(refunds.approve_refund, refund.pk, staff=manager)
    refund.refresh_from_db()
    return refund


# --- Happy paths ---------------------------------------------------------------------------------


def test_full_refund_flow(paid, desk, manager, run_on_commit):
    refund = refunds.request_refund(
        paid.pk, staff=desk, amount_paise=236_000, reason="Session missed"
    )
    assert refund.status == Refund.Status.REQUESTED
    assert refresh(paid.invoice).amount_refunded_paise == 0  # nothing moves before approval

    refund = approve(refund, manager, run_on_commit)
    assert refund.status == Refund.Status.PROCESSED
    assert refund.gateway_refund_id.startswith("rfnd_")
    assert refund.approved_by == manager

    invoice = refresh(paid.invoice)
    assert invoice.status == InvoiceStatus.REFUNDED
    assert invoice.amount_refunded_paise == 236_000
    debit = LedgerEntry.objects.get(entry_type="refund")
    assert debit.debit_paise == 236_000 and debit.refund == refund
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"refund.requested", "refund.approved", "refund.processed"} <= actions


def test_partial_refunds(paid, desk, manager, run_on_commit):
    first = refunds.request_refund(paid.pk, staff=desk, amount_paise=36_000, reason="Discount")
    approve(first, manager, run_on_commit)
    assert refresh(paid.invoice).status == InvoiceStatus.PARTIALLY_REFUNDED

    rest = refunds.request_refund(paid.pk, staff=desk, amount_paise=200_000, reason="Cancelled")
    approve(rest, manager, run_on_commit)
    invoice = refresh(paid.invoice)
    assert invoice.status == InvoiceStatus.REFUNDED
    assert invoice.amount_refunded_paise == 236_000


def test_refund_applied_once_from_api_response_and_webhook(paid, desk, manager, run_on_commit):
    """The sandbox (like Razorpay) answers 'processed' AND sends refund.processed."""
    refund = approve(
        refunds.request_refund(paid.pk, staff=desk, amount_paise=10_000, reason="x"),
        manager,
        run_on_commit,
    )
    assert WebhookEvent.objects.filter(event_type="refund.processed").count() == 1
    assert LedgerEntry.objects.filter(refund=refund).count() == 1
    assert refresh(paid.invoice).amount_refunded_paise == 10_000


def test_rejected_refund_moves_no_money(paid, desk, manager):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=10_000, reason="x")
    refunds.reject_refund(refund.pk, staff=manager, note="Not eligible")
    refund.refresh_from_db()
    assert refund.status == Refund.Status.REJECTED
    assert FakeRefund.objects.count() == 0
    # and the reserved amount is released
    assert refunds.refundable_paise(paid) == 236_000


# --- Rules ----------------------------------------------------------------------------------------


def test_refund_cannot_exceed_paid(paid, desk):
    """Edge case 11: rejected before the gateway is called."""
    with pytest.raises(refunds.RefundExceedsPaid):
        refunds.request_refund(paid.pk, staff=desk, amount_paise=236_001, reason="x")
    assert FakeRefund.objects.count() == 0


def test_pending_refunds_reserve_the_amount(paid, desk):
    refunds.request_refund(paid.pk, staff=desk, amount_paise=200_000, reason="a")
    with pytest.raises(refunds.RefundExceedsPaid):
        refunds.request_refund(paid.pk, staff=desk, amount_paise=40_000, reason="b")


def test_only_managers_decide(paid, desk):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=1_000, reason="x")
    from apps.common.exceptions import ManagerRequired

    with pytest.raises(ManagerRequired):
        refunds.approve_refund(refund.pk, staff=desk)
    with pytest.raises(ManagerRequired):
        refunds.reject_refund(refund.pk, staff=desk, note="x")


def test_cannot_decide_twice(paid, desk, manager, run_on_commit):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=1_000, reason="x")
    approve(refund, manager, run_on_commit)
    with pytest.raises(refunds.RefundNotPending):
        refunds.approve_refund(refund.pk, staff=manager)
    with pytest.raises(refunds.RefundNotPending):
        refunds.reject_refund(refund.pk, staff=manager, note="x")


def test_cannot_refund_failed_payment(issued_invoice, sandbox_collect, customer_pays, desk):
    customer_pays(sandbox_collect(issued_invoice), "fail")
    failed = Payment.objects.get()
    with pytest.raises(refunds.BusinessValidationError):
        refunds.request_refund(failed.pk, staff=desk, amount_paise=100, reason="x")


# --- Edge cases -----------------------------------------------------------------------------------


def test_refund_of_overpayment_keeps_invoice_paid(
    issued_invoice, sandbox_collect, customer_pays, desk, manager, run_on_commit
):
    first = sandbox_collect(issued_invoice)
    second = sandbox_collect(issued_invoice, amount_paise=236_000 - 100)  # a second live link
    # Both paid before the "cancel the other link" task reached the gateway.
    with mock.patch("apps.payments.tasks.cancel_superseded_links.delay"):
        customer_pays(first)
        customer_pays(second)
    issue = ReconciliationIssue.objects.get(kind="overpaid")
    extra = Payment.objects.get(attempt=second)

    approve(
        refunds.request_refund(extra.pk, staff=desk, amount_paise=235_900, reason="Paid twice"),
        manager,
        run_on_commit,
    )
    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PAID  # still fully paid, just not overpaid
    assert invoice.amount_paid_paise - invoice.amount_refunded_paise == invoice.total_paise
    issue.refresh_from_db()
    assert issue.status == "resolved"


def test_cash_refund_is_immediate(issued_invoice, desk, manager):
    payment, _ = payments.record_cash_payment(
        issued_invoice.pk, staff=desk, amount_paise=None, idempotency_key="cash-1"
    )
    refund = refunds.request_refund(payment.pk, staff=desk, amount_paise=236_000, reason="x")
    refunds.approve_refund(refund.pk, staff=manager)  # no gateway, no Celery
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    assert refund.gateway_refund_id is None
    assert refresh(issued_invoice).status == InvoiceStatus.REFUNDED


def test_out_of_order_refund_retries(paid, run_on_commit):
    """Edge case 4: refund.processed arrives for a payment we haven't recorded yet."""
    ghost_payment = rzp.payment_entity(amount=50_000)
    refund_event = rzp.event(
        "refund.processed",
        refund={
            "id": "rfnd_early",
            "entity": "refund",
            "amount": 50_000,
            "payment_id": ghost_payment["id"],
            "notes": [],
            "status": "processed",
        },
    )
    event = WebhookEvent.objects.create(
        gateway_event_id="evt_refund_early", event_type="refund.processed", raw_payload=refund_event
    )
    with pytest.raises(refunds.DependencyNotReady):
        process_event(event.pk)
    event.refresh_from_db()
    assert event.processed_at is None

    # The payment shows up (its webhook was just slow)...
    payments.apply_payment_captured(
        gateway_payment_id=ghost_payment["id"],
        invoice_id=paid.invoice_id,
        amount_paise=50_000,
    )
    # ...and the retry now applies the refund, recording it as made outside CentrePay.
    assert process_event(event.pk) == "processed"
    refund = Refund.objects.get(gateway_refund_id="rfnd_early")
    assert refund.status == Refund.Status.PROCESSED
    assert refund.requested_by is None
    assert ReconciliationIssue.objects.filter(
        kind="status_mismatch", gateway_ref="rfnd_early"
    ).exists()


def test_orphaned_refund_gives_up_with_issue(db):
    event = WebhookEvent.objects.create(
        gateway_event_id="evt_orphan",
        event_type="refund.processed",
        raw_payload=rzp.event(
            "refund.processed",
            refund={"id": "rfnd_orphan", "amount": 500, "payment_id": "pay_never", "notes": []},
        ),
    )
    celery_app.conf.update(task_eager_propagates=False, CELERY_TASK_EAGER_PROPAGATES=False)
    try:
        result = process_webhook_event.apply(args=(event.pk,))
    finally:
        celery_app.conf.update(task_eager_propagates=True, CELERY_TASK_EAGER_PROPAGATES=True)
    assert result.result == "gave_up"
    event.refresh_from_db()
    assert event.processed_at is not None
    assert event.last_error.startswith("gave up")
    issue = ReconciliationIssue.objects.get(kind="refund_orphaned")
    assert issue.gateway_ref == "rfnd_orphan"


def test_refund_failed_webhook(paid, desk, manager, run_on_commit, sandbox):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=1_000, reason="x")
    with mock.patch("apps.payments.tasks.process_refund.delay"):  # gateway call pending
        run_on_commit(refunds.approve_refund, refund.pk, staff=manager)
    Refund.objects.filter(pk=refund.pk).update(status="processing", gateway_refund_id="rfnd_x")
    refunds.apply_refund_failed(gateway_refund_id="rfnd_x", error="Bank rejected")
    refund.refresh_from_db()
    assert refund.status == Refund.Status.FAILED
    assert refunds.refundable_paise(paid) == 236_000  # released
    assert refresh(paid.invoice).amount_refunded_paise == 0


# --- Real Razorpay client paths -------------------------------------------------------------------


def _razorpay_paid_invoice(issued_invoice):
    payment, _ = payments.apply_payment_captured(
        gateway_payment_id="pay_REAL0000001", invoice_id=issued_invoice.pk, amount_paise=236_000
    )
    return payment


def test_gateway_timeout_then_retry_finds_refund_by_receipt(issued_invoice, desk, manager, gateway):
    payment = _razorpay_paid_invoice(issued_invoice)
    refund = refunds.request_refund(payment.pk, staff=desk, amount_paise=1_000, reason="x")
    Refund.objects.filter(pk=refund.pk).update(status="approved")
    refund.refresh_from_db()

    gateway.add(responses.GET, f"{RZP}/payments/pay_REAL0000001/refunds", json={"items": []})
    gateway.add(
        responses.POST,
        f"{RZP}/payments/pay_REAL0000001/refund",
        body=requests.Timeout("read timeout"),
    )
    with pytest.raises(refunds.GatewayError):
        refunds.submit_to_gateway(refund.pk)
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSING and refund.gateway_refund_id is None

    # Razorpay did create it. The retry finds it by receipt instead of refunding twice.
    made = {
        "id": "rfnd_REAL01",
        "amount": 1_000,
        "receipt": refund.receipt,
        "status": "processed",
        "notes": {"refund_id": str(refund.pk)},
    }
    gateway.replace(
        responses.GET, f"{RZP}/payments/pay_REAL0000001/refunds", json={"items": [made]}
    )
    assert refunds.submit_to_gateway(refund.pk) == "processed"
    refund.refresh_from_db()
    assert refund.status == Refund.Status.PROCESSED
    creations = [c for c in gateway.calls if c.request.url.endswith("/refund")]
    assert len(creations) == 1  # only the timed-out original


def test_gateway_rejects_refund(issued_invoice, desk, gateway):
    payment = _razorpay_paid_invoice(issued_invoice)
    refund = refunds.request_refund(payment.pk, staff=desk, amount_paise=1_000, reason="x")
    Refund.objects.filter(pk=refund.pk).update(status="approved")
    gateway.add(responses.GET, f"{RZP}/payments/pay_REAL0000001/refunds", json={"items": []})
    gateway.add(
        responses.POST,
        f"{RZP}/payments/pay_REAL0000001/refund",
        status=400,
        json={"error": {"code": "BAD_REQUEST_ERROR", "description": "Payment is under dispute"}},
    )
    assert refunds.submit_to_gateway(refund.pk) == "failed"
    refund.refresh_from_db()
    assert refund.status == Refund.Status.FAILED
    assert "dispute" in refund.last_error


# --- API ------------------------------------------------------------------------------------------


def test_refund_api_flow(paid, desk, manager, api_as, django_capture_on_commit_callbacks):
    resp = api_as(desk).post(
        f"/api/v1/payments/{paid.pk}/refunds/",
        {"amount_paise": 50_000, "reason": "Unhappy"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    refund_id = resp.json()["id"]

    queue = api_as(manager).get("/api/v1/refunds/?status=requested").json()["results"]
    assert [r["id"] for r in queue] == [refund_id]

    assert_error(
        api_as(desk).post(f"/api/v1/refunds/{refund_id}/approve/"), 403, "PERMISSION_DENIED"
    )
    with django_capture_on_commit_callbacks(execute=True):
        resp = api_as(manager).post(f"/api/v1/refunds/{refund_id}/approve/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"  # response is written before the task ran

    detail = api_as(desk).get(f"/api/v1/invoices/{paid.invoice_id}/").json()
    assert detail["status"] == "partially_refunded"
    assert detail["refunds"][0]["status"] == "processed"


def test_refund_api_rejects_excess_and_other_centre(paid, desk, api_as):
    from .factories import StaffFactory

    resp = api_as(desk).post(
        f"/api/v1/payments/{paid.pk}/refunds/",
        {"amount_paise": 999_999, "reason": "x"},
        format="json",
    )
    assert_error(resp, 400, "REFUND_EXCEEDS_PAID")

    outsider = StaffFactory(role="manager")
    resp = api_as(outsider).post(
        f"/api/v1/payments/{paid.pk}/refunds/", {"amount_paise": 1, "reason": "x"}, format="json"
    )
    assert_error(resp, 404, "NOT_FOUND")


def test_reject_via_api_requires_note(paid, desk, manager, api_as):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=1_000, reason="x")
    assert_error(
        api_as(manager).post(f"/api/v1/refunds/{refund.pk}/reject/", {}), 400, "VALIDATION_ERROR"
    )
    resp = api_as(manager).post(f"/api/v1/refunds/{refund.pk}/reject/", {"note": "No"})
    assert resp.json()["status"] == "rejected"


# --- Cash -------------------------------------------------------------------------------------


def test_cash_payment_is_idempotent(issued_invoice, desk, api_as):
    client = api_as(desk)
    key = {"HTTP_IDEMPOTENCY_KEY": uuid.uuid4().hex}
    first = client.post(f"/api/v1/invoices/{issued_invoice.pk}/cash/", {}, format="json", **key)
    second = client.post(f"/api/v1/invoices/{issued_invoice.pk}/cash/", {}, format="json", **key)
    assert (first.status_code, second.status_code) == (201, 200)
    assert Payment.objects.get().method == "cash"
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_cash_cannot_exceed_due(issued_invoice, desk, api_as):
    resp = api_as(desk).post(
        f"/api/v1/invoices/{issued_invoice.pk}/cash/",
        {"amount_paise": 236_001},
        format="json",
        HTTP_IDEMPOTENCY_KEY="k",
    )
    assert_error(resp, 400, "VALIDATION_ERROR")
