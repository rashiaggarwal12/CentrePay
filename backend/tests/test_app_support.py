"""Endpoints that exist so the mobile app never has to compute money itself."""

import pytest

from apps.payments import refunds
from apps.payments.models import Payment

from .test_api import assert_error

pytestmark = pytest.mark.django_db


def test_preview_matches_what_create_would_save(api_as, desk, customer, service, exempt_service):
    body = {
        "items": [{"service": service.id, "qty": 1}, {"service": exempt_service.id, "qty": 2}],
        "discount_paise": 10_000,
    }
    client = api_as(desk)
    preview = client.post("/api/v1/invoices/preview/", body, format="json").json()
    created = client.post(
        "/api/v1/invoices/", {**body, "customer": customer.id}, format="json"
    ).json()
    for key in ("subtotal_paise", "discount_paise", "tax_paise", "total_paise"):
        assert preview[key] == created[key]
    assert [line["tax_paise"] for line in preview["lines"]] == [
        item["tax_paise"] for item in created["items"]
    ]


def test_preview_saves_nothing_and_allows_empty(api_as, desk):
    from apps.billing.models import Invoice

    resp = api_as(desk).post("/api/v1/invoices/preview/", {"items": []}, format="json")
    assert resp.json()["total_paise"] == 0
    assert Invoice.objects.count() == 0


def test_preview_rejects_excess_discount(api_as, desk, service):
    resp = api_as(desk).post(
        "/api/v1/invoices/preview/",
        {"items": [{"service": service.id, "qty": 1}], "discount_paise": 999_999},
        format="json",
    )
    assert_error(resp, 400, "VALIDATION_ERROR")


def test_timeline(
    api_as, desk, manager, issued_invoice, sandbox_collect, customer_pays, run_on_commit
):
    customer_pays(sandbox_collect(issued_invoice))
    payment = Payment.objects.get()
    run_on_commit(
        refunds.approve_refund,
        refunds.request_refund(payment.pk, staff=desk, amount_paise=1_000, reason="Late").pk,
        staff=manager,
    )
    events = api_as(desk).get(f"/api/v1/invoices/{issued_invoice.pk}/timeline/").json()
    assert [e["action"] for e in events] == [
        "invoice.created",
        "invoice.issued",
        "payment_attempt.created",
        "payment.captured",
        "refund.requested",
        "refund.approved",
        "refund.processed",
    ]
    captured = events[3]
    assert captured["detail"] == "UPI" and captured["amount_paise"] == 236_000
    assert captured["actor"] == "System"  # applied by the webhook, not a person
    assert events[4]["detail"] == "Late"
    assert events[1]["detail"] == issued_invoice.number


def test_refundable_amount_in_invoice_detail(
    api_as, desk, issued_invoice, sandbox_collect, customer_pays
):
    customer_pays(sandbox_collect(issued_invoice))
    payment = Payment.objects.get()
    refunds.request_refund(payment.pk, staff=desk, amount_paise=36_000, reason="x")
    detail = api_as(desk).get(f"/api/v1/invoices/{issued_invoice.pk}/").json()
    assert detail["payments"][0]["refundable_paise"] == 200_000  # pending refund reserved
