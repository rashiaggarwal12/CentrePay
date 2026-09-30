"""The daily job against the sandbox gateway, with mismatches planted on either side."""

from datetime import timedelta

import pytest
import responses
from django.core.management import call_command
from django.utils import timezone

from apps.billing.models import Invoice, InvoiceStatus
from apps.payments import refunds
from apps.payments import services as payments
from apps.payments.models import Payment
from apps.reconciliation.engine import run_reconciliation
from apps.reconciliation.models import ReconciliationIssue, ReconciliationRun
from apps.sandbox.gateway import fake_id, fee_for
from apps.sandbox.models import FakePayment, FakeRefund
from apps.webhooks.models import WebhookEvent

from . import razorpay as rzp
from .conftest import RZP

pytestmark = pytest.mark.django_db


def today():
    return timezone.localdate()


def issues(kind=None):
    qs = ReconciliationIssue.objects.filter(status="open")
    return qs.filter(kind=kind) if kind else qs


@pytest.fixture
def paid(issued_invoice, sandbox_collect, customer_pays):
    customer_pays(sandbox_collect(issued_invoice))
    return Payment.objects.get(invoice=issued_invoice)


def test_clean_day_has_no_issues(paid, sandbox):
    run = run_reconciliation(today())
    assert run.status == ReconciliationRun.Status.SUCCEEDED
    assert run.summary["issues_total"] == 0
    assert run.summary["gateway_payments"] == 1
    assert run.summary["invoices_checked"] == 1
    assert not issues().exists()


def test_reconciliation_recovers_missing_payment(
    issued_invoice, sandbox_collect, customer_pays, sandbox
):
    """Edge case 7: the customer paid, no webhook ever arrived. The job finds the
    payment through the gateway API and applies it through the normal service."""
    customer_pays(sandbox_collect(issued_invoice), "pay_lost_webhooks")
    assert Invoice.objects.get(pk=issued_invoice.pk).status == InvoiceStatus.ISSUED

    run = run_reconciliation(today())
    assert run.summary["recovered_payments"] == 1
    invoice = Invoice.objects.get(pk=issued_invoice.pk)
    assert invoice.status == InvoiceStatus.PAID
    assert Payment.objects.get().fee_paise == FakePayment.objects.get().fee
    assert run.summary["issues_total"] == 0

    # Idempotent: a second run changes nothing.
    again = run_reconciliation(today())
    assert "recovered_payments" not in again.summary
    assert Invoice.objects.get(pk=issued_invoice.pk).amount_paid_paise == 236_000


def test_recovers_link_payment_even_without_notes(
    issued_invoice, sandbox_collect, customer_pays, sandbox
):
    """If the gateway's payment carries no notes, the open-link sweep still finds it."""
    attempt = sandbox_collect(issued_invoice)
    customer_pays(attempt, "pay_lost_webhooks")
    FakePayment.objects.update(notes={})

    run = run_reconciliation(today())
    assert run.summary["recovered_payments"] == 1
    assert run.summary["links_checked"] == 1
    assert Invoice.objects.get(pk=issued_invoice.pk).status == InvoiceStatus.PAID


def test_unidentifiable_gateway_payment_raises_missing_locally(sandbox, db):
    fee, tax = fee_for(5_000)
    FakePayment.objects.create(
        id="pay_Sstranger01",
        order_id="order_x",
        amount=5_000,
        method="upi",
        status="captured",
        fee=fee,
        tax=tax,
        created_at=timezone.now(),
    )
    run_reconciliation(today())
    issue = issues("missing_locally").get()
    assert issue.gateway_ref == "pay_Sstranger01"
    assert issue.actual_paise == 5_000


def test_local_payment_missing_at_gateway(issued_invoice, sandbox):
    payments.apply_payment_captured(
        gateway_payment_id="pay_Sghost00001", invoice_id=issued_invoice.pk, amount_paise=1_000
    )
    run_reconciliation(today())
    assert issues("missing_at_gateway").get().gateway_ref == "pay_Sghost00001"


def test_cash_payments_are_not_expected_at_gateway(issued_invoice, desk, sandbox):
    payments.record_cash_payment(
        issued_invoice.pk, staff=desk, amount_paise=None, idempotency_key="c1"
    )
    run = run_reconciliation(today())
    assert run.summary["issues_total"] == 0


def test_amount_mismatch_detected(paid, sandbox):
    FakePayment.objects.filter(pk=paid.gateway_payment_id).update(amount=235_000)
    run_reconciliation(today())
    issue = issues("amount_mismatch").get()
    assert (issue.expected_paise, issue.actual_paise) == (236_000, 235_000)


def test_gateway_status_mismatch(paid, sandbox):
    FakePayment.objects.filter(pk=paid.gateway_payment_id).update(status="failed")
    run_reconciliation(today())
    assert issues("status_mismatch").filter(gateway_ref=paid.gateway_payment_id).exists()


def test_refund_made_at_gateway_but_not_recorded(paid, sandbox):
    """Someone refunded from the gateway dashboard and the webhook was lost."""
    FakeRefund.objects.create(
        id=fake_id("rfnd"),
        payment_id=paid.gateway_payment_id,
        amount=10_000,
        created_at=timezone.now(),
    )
    FakePayment.objects.filter(pk=paid.gateway_payment_id).update(amount_refunded=10_000)
    run_reconciliation(today())
    issue = issues("status_mismatch").get()
    assert (issue.expected_paise, issue.actual_paise) == (0, 10_000)


def test_refunds_that_match_raise_nothing(paid, desk, manager, run_on_commit, sandbox):
    refund = refunds.request_refund(paid.pk, staff=desk, amount_paise=10_000, reason="x")
    run_on_commit(refunds.approve_refund, refund.pk, staff=manager)
    run = run_reconciliation(today())
    assert run.summary["issues_total"] == 0


def test_invoice_drift_detected(paid, sandbox):
    Invoice.objects.filter(pk=paid.invoice_id).update(amount_paid_paise=100_000)
    run_reconciliation(today())
    issue = issues("invoice_drift").get()
    assert issue.invoice_id == paid.invoice_id
    assert issue.details["mismatches"]["amount_paid"] == [100_000, 236_000]
    assert "ledger_net" in issue.details["mismatches"]


def test_settlement_matches_next_day(paid, sandbox):
    run = run_reconciliation(today() + timedelta(days=1))  # sandbox settles T+1
    assert run.summary["settlements_checked"] == 1
    assert not issues("settlement_mismatch").exists()


def test_settlement_fee_mismatch(paid, sandbox):
    FakePayment.objects.filter(pk=paid.gateway_payment_id).update(fee=paid.fee_paise + 500)
    run_reconciliation(today() + timedelta(days=1))
    kinds = list(issues("settlement_mismatch").values_list("gateway_ref", flat=True))
    assert paid.gateway_payment_id in kinds  # the fee on the payment
    assert any(ref.startswith("setl_") for ref in kinds)  # and the settlement total


def test_stuck_event(sandbox, db):
    event = WebhookEvent.objects.create(
        gateway_event_id="evt_stuck", event_type="payment.captured", raw_payload={}
    )
    WebhookEvent.objects.filter(pk=event.pk).update(received_at=timezone.now() - timedelta(hours=2))
    run_reconciliation(today())
    assert issues("stuck_event").get().gateway_ref == "evt_stuck"


def test_repeated_runs_do_not_duplicate_issues(paid, sandbox):
    FakePayment.objects.filter(pk=paid.gateway_payment_id).update(amount=1)
    run_reconciliation(today())
    run_reconciliation(today())
    assert issues("amount_mismatch").count() == 1


def test_gateway_down_still_runs_local_checks(paid, settings):
    settings.PAYMENT_GATEWAY = "razorpay"
    settings.RAZORPAY_KEY_ID = ""  # not configured
    Invoice.objects.filter(pk=paid.invoice_id).update(amount_paid_paise=1)
    run = run_reconciliation(today())
    assert run.status == ReconciliationRun.Status.FAILED
    assert "error" in run.summary
    assert issues("invoice_drift").exists()


def test_reconcile_command(paid, sandbox, capsys):
    call_command("reconcile", "--date", today().isoformat())
    out = capsys.readouterr().out
    assert f"Reconciliation {today()}: succeeded" in out


# --- Real Razorpay client: pagination and request shapes ------------------------------------------


def test_razorpay_client_paginates_payments(gateway, db):
    from apps.payments.gateway import RazorpayClient

    page1 = [rzp.payment_entity() for _ in range(2)]
    page2 = [rzp.payment_entity()]
    gateway.add(responses.GET, f"{RZP}/payments", json={"items": page1})
    gateway.add(responses.GET, f"{RZP}/payments", json={"items": page2})
    client = RazorpayClient("rzp_test_x", "secret")
    ids = [p["id"] for p in client.iter_payments(0, 1, page_size=2)]
    assert ids == [p["id"] for p in page1 + page2]
    assert "skip=2" in gateway.calls[1].request.url


def test_daily_collection_report(
    issued_invoice, make_draft, desk, manager, sandbox_collect, customer_pays, run_on_commit, api_as
):
    from apps.billing import services as billing

    customer_pays(sandbox_collect(issued_invoice))  # ₹2,360 UPI
    second = billing.issue_invoice(make_draft().pk, staff=desk)
    payments.record_cash_payment(second.pk, staff=desk, amount_paise=None, idempotency_key="c")
    upi = Payment.objects.get(method="upi")
    run_on_commit(
        refunds.approve_refund,
        refunds.request_refund(upi.pk, staff=desk, amount_paise=36_000, reason="x").pk,
        staff=manager,
    )

    report = api_as(desk).get("/api/v1/reports/daily-collection/").json()
    assert report["collections"]["by_method"]["upi"]["amount_paise"] == 236_000
    assert report["collections"]["by_method"]["cash"]["amount_paise"] == 236_000
    assert report["collections"]["total_paise"] == 472_000
    assert report["refunds"] == {"count": 1, "total_paise": 36_000}
    assert report["net_collection_paise"] == 436_000
    assert report["gateway_fees_paise"] == upi.fee_paise
    assert report["invoices"]["issued"] == 2

    other_day = api_as(desk).get("/api/v1/reports/daily-collection/?date=2020-01-01").json()
    assert other_day["collections"]["total_paise"] == 0
    bad = api_as(desk).get("/api/v1/reports/daily-collection/?date=yesterday")
    assert bad.status_code == 400
