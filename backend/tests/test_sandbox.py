"""End-to-end flows through the sandbox gateway: collect -> customer pays on the fake
payment page -> signed webhooks -> invoice paid. Nothing here is mocked."""

import pytest

from apps.billing.models import Invoice, InvoiceStatus
from apps.ledger.models import LedgerEntry
from apps.payments.models import Payment, PaymentAttempt
from apps.reconciliation.models import ReconciliationIssue
from apps.sandbox.models import FakeLink, FakePayment
from apps.webhooks.models import WebhookEvent

pytestmark = pytest.mark.django_db


def refresh(invoice):
    return Invoice.objects.get(pk=invoice.pk)


def test_collect_creates_sandbox_link(issued_invoice, sandbox_collect):
    attempt = sandbox_collect(issued_invoice)
    assert attempt.status == PaymentAttempt.Status.CREATED
    assert attempt.short_url == f"http://testserver/sandbox/pay/{attempt.gateway_link_id}/"
    link = FakeLink.objects.get(pk=attempt.gateway_link_id)
    assert link.amount == 236_000
    assert link.reference_id == attempt.reference_id


def test_customer_pays_and_invoice_becomes_paid(
    issued_invoice, sandbox_collect, customer_pays, client
):
    attempt = sandbox_collect(issued_invoice)
    page = client.get(f"/sandbox/pay/{attempt.gateway_link_id}/")
    assert page.status_code == 200
    assert "₹2,360.00" in page.content.decode()

    customer_pays(attempt, "pay_upi")

    invoice = refresh(issued_invoice)
    assert invoice.status == InvoiceStatus.PAID
    payment = Payment.objects.get()
    assert payment.method == "upi"
    assert payment.fee_paise == FakePayment.objects.get().fee  # fee carried in the webhook
    # payment.captured + payment_link.paid, both signed and processed
    assert set(WebhookEvent.objects.values_list("event_type", flat=True)) == {
        "payment.captured",
        "payment_link.paid",
    }
    assert WebhookEvent.objects.filter(processed_at__isnull=True).count() == 0


def test_duplicate_webhooks_scenario(issued_invoice, sandbox_collect, customer_pays):
    customer_pays(sandbox_collect(issued_invoice), "pay_duplicate_webhooks")
    assert WebhookEvent.objects.count() == 2  # 6 deliveries, 2 distinct events
    assert Payment.objects.count() == 1
    assert LedgerEntry.objects.count() == 1
    assert refresh(issued_invoice).amount_paid_paise == 236_000


def test_failed_payment_leaves_invoice_open(issued_invoice, sandbox_collect, customer_pays):
    attempt = sandbox_collect(issued_invoice)
    customer_pays(attempt, "fail")
    assert Payment.objects.get().status == Payment.Status.FAILED
    assert refresh(issued_invoice).status == InvoiceStatus.ISSUED
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.CREATED  # the customer can retry

    customer_pays(attempt, "pay_card")
    assert refresh(issued_invoice).status == InvoiceStatus.PAID


def test_short_payment_raises_amount_mismatch(issued_invoice, sandbox_collect, customer_pays):
    customer_pays(sandbox_collect(issued_invoice), "pay_short")
    assert ReconciliationIssue.objects.filter(kind="amount_mismatch").exists()
    assert refresh(issued_invoice).status == InvoiceStatus.PARTIALLY_PAID


def test_expire_link(issued_invoice, sandbox_collect, customer_pays):
    attempt = sandbox_collect(issued_invoice)
    customer_pays(attempt, "expire")
    attempt.refresh_from_db()
    assert attempt.status == PaymentAttempt.Status.EXPIRED


def test_lost_webhooks_leave_invoice_unpaid(issued_invoice, sandbox_collect, customer_pays):
    customer_pays(sandbox_collect(issued_invoice), "pay_lost_webhooks")
    assert FakePayment.objects.count() == 1  # the customer paid...
    assert Payment.objects.count() == 0  # ...but we never heard about it
    assert refresh(issued_invoice).status == InvoiceStatus.ISSUED


def test_cancelled_invoice_link_cannot_be_paid(
    issued_invoice, sandbox_collect, manager, run_on_commit, client
):
    from apps.billing import services as billing

    attempt = sandbox_collect(issued_invoice)
    run_on_commit(billing.cancel_invoice, issued_invoice.pk, staff=manager, reason="left")
    assert FakeLink.objects.get(pk=attempt.gateway_link_id).status == "cancelled"
    page = client.get(f"/sandbox/pay/{attempt.gateway_link_id}/").content.decode()
    assert "Pay with UPI" not in page


def test_sandbox_disabled_with_real_gateway(client, settings):
    settings.PAYMENT_GATEWAY = "razorpay"
    assert client.get("/sandbox/").status_code == 404


def test_sandbox_index_lists_links(issued_invoice, sandbox_collect, client):
    attempt = sandbox_collect(issued_invoice)
    page = client.get("/sandbox/").content.decode()
    assert attempt.gateway_link_id in page
