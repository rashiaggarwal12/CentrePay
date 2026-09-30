import json
import uuid

import pytest
import requests
import responses

from apps.billing import services as billing
from apps.payments.models import PaymentAttempt

from . import razorpay as rzp
from .conftest import RZP
from .test_api import assert_error

pytestmark = pytest.mark.django_db


def collect_via_api(client, invoice, key="key-1", **body):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return client.post(f"/api/v1/invoices/{invoice.id}/collect/", body, format="json", **headers)


def link_creations(gateway):
    return [
        c
        for c in gateway.calls
        if c.request.method == "POST" and c.request.url.endswith("/payment_links")
    ]


def test_collect_creates_payment_link(api_as, desk, issued_invoice, mock_link_creation):
    resp = collect_via_api(api_as(desk), issued_invoice)
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["status"] == "created"
    assert body["amount_paise"] == 236_000
    assert body["short_url"].startswith("https://rzp.io/")

    sent = json.loads(link_creations(mock_link_creation)[0].request.body)
    assert sent["amount"] == 236_000
    assert sent["currency"] == "INR"
    assert sent["accept_partial"] is False
    assert sent["notes"]["invoice_id"] == str(issued_invoice.id)
    assert sent["customer"]["contact"] == issued_invoice.customer.phone
    attempt = PaymentAttempt.objects.get()
    assert sent["reference_id"] == attempt.reference_id
    assert attempt.gateway_link_id == body["gateway_link_id"]


def test_collect_is_idempotent(api_as, desk, issued_invoice, mock_link_creation):
    """Edge case 12: a retry or double tap with the same key returns the same link."""
    client = api_as(desk)
    first = collect_via_api(client, issued_invoice, key="tap-1")
    second = collect_via_api(client, issued_invoice, key="tap-1")
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(link_creations(mock_link_creation)) == 1
    assert PaymentAttempt.objects.count() == 1


def test_second_staff_member_gets_the_live_link(
    api_as, desk, manager, issued_invoice, mock_link_creation
):
    """Two staff collect the same invoice with different keys: one QR, not two."""
    a = collect_via_api(api_as(desk), issued_invoice, key="desk-key").json()
    b = collect_via_api(api_as(manager), issued_invoice, key="manager-key").json()
    assert a["id"] == b["id"]
    assert len(link_creations(mock_link_creation)) == 1


def test_collect_requires_idempotency_key(api_as, desk, issued_invoice, mock_link_creation):
    assert_error(collect_via_api(api_as(desk), issued_invoice, key=None), 400, "VALIDATION_ERROR")


def test_idempotency_key_reuse_for_other_invoice_rejected(
    api_as, desk, make_draft, issued_invoice, mock_link_creation
):
    other = billing.issue_invoice(make_draft().pk, staff=desk)
    client = api_as(desk)
    collect_via_api(client, issued_invoice, key="same")
    assert_error(collect_via_api(client, other, key="same"), 422, "IDEMPOTENCY_KEY_REUSED")


def test_partial_collection(api_as, desk, issued_invoice, mock_link_creation):
    resp = collect_via_api(api_as(desk), issued_invoice, amount_paise=100_000)
    assert resp.status_code == 201
    assert resp.json()["amount_paise"] == 100_000


def test_cannot_collect_more_than_due(api_as, desk, issued_invoice, mock_link_creation):
    resp = collect_via_api(api_as(desk), issued_invoice, amount_paise=236_001)
    assert_error(resp, 400, "VALIDATION_ERROR")


def test_cannot_collect_on_draft(api_as, desk, make_draft, mock_link_creation):
    resp = collect_via_api(api_as(desk), make_draft())
    assert_error(resp, 409, "INVALID_TRANSITION")
    assert PaymentAttempt.objects.count() == 0


def test_cannot_collect_other_centres_invoice(api_as, issued_invoice, mock_link_creation):
    from .factories import StaffFactory

    resp = collect_via_api(api_as(StaffFactory()), issued_invoice)
    assert_error(resp, 404, "NOT_FOUND")


def test_gateway_error_then_retry_succeeds(api_as, desk, issued_invoice, gateway):
    client = api_as(desk)
    gateway.add(
        responses.POST,
        f"{RZP}/payment_links",
        status=500,
        json={"error": {"code": "SERVER_ERROR", "description": "Something went wrong"}},
    )
    resp = collect_via_api(client, issued_invoice, key="k")
    assert_error(resp, 502, "GATEWAY_ERROR")
    attempt = PaymentAttempt.objects.get()
    assert attempt.status == "failed"
    assert "Something went wrong" in attempt.last_error

    # Retry with the same key: look the link up first (none), then create it.
    gateway.replace(
        responses.POST,
        f"{RZP}/payment_links",
        json=rzp.link_response(reference_id=attempt.reference_id, amount=236_000),
    )
    gateway.add(responses.GET, f"{RZP}/payment_links", json={"payment_links": []})
    resp = collect_via_api(client, issued_invoice, key="k")
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"
    assert PaymentAttempt.objects.count() == 1


def test_retry_recovers_link_created_before_timeout(api_as, desk, issued_invoice, gateway):
    """Razorpay created the link but our request timed out. The retry must find that
    link instead of creating a second one the customer could also pay."""
    client = api_as(desk)
    gateway.add(responses.POST, f"{RZP}/payment_links", body=requests.Timeout("read timeout"))
    assert_error(collect_via_api(client, issued_invoice, key="k"), 502, "GATEWAY_ERROR")
    attempt = PaymentAttempt.objects.get()

    existing = rzp.link_response(reference_id=attempt.reference_id, amount=236_000)
    gateway.add(responses.GET, f"{RZP}/payment_links", json={"payment_links": [existing]})
    resp = collect_via_api(client, issued_invoice, key="k")
    assert resp.status_code == 200
    assert resp.json()["gateway_link_id"] == existing["id"]
    assert len(link_creations(gateway)) == 1  # only the original, timed-out call


def test_gateway_not_configured(api_as, desk, issued_invoice, settings):
    settings.RAZORPAY_KEY_ID = ""
    assert_error(collect_via_api(api_as(desk), issued_invoice), 503, "GATEWAY_NOT_CONFIGURED")


def test_cancelling_invoice_cancels_live_links(
    manager, issued_invoice, collect, mock_link_creation, django_capture_on_commit_callbacks
):
    attempt = collect(issued_invoice)
    with django_capture_on_commit_callbacks(execute=True):
        billing.cancel_invoice(issued_invoice.pk, staff=manager, reason="Customer left")
    cancel_calls = [c for c in mock_link_creation.calls if c.request.url.endswith("/cancel")]
    assert [c.request.url for c in cancel_calls] == [
        f"{RZP}/payment_links/{attempt.gateway_link_id}/cancel"
    ]
    attempt.refresh_from_db()
    assert attempt.status == "cancelled"


def test_invoice_detail_shows_attempts(api_as, desk, issued_invoice, collect):
    collect(issued_invoice, key=uuid.uuid4().hex)
    body = api_as(desk).get(f"/api/v1/invoices/{issued_invoice.id}/").json()
    assert len(body["payment_attempts"]) == 1
    assert body["payments"] == []


def test_retry_while_first_request_in_flight_is_rejected(api_as, desk, issued_invoice, gateway):
    """A fresh 'pending' attempt means another request is mid-call to Razorpay.
    A concurrent retry must not start a second gateway call."""
    PaymentAttempt.objects.create(
        invoice=issued_invoice,
        amount_paise=236_000,
        idempotency_key="k",
        reference_id="cp_inflight",
    )
    assert_error(collect_via_api(api_as(desk), issued_invoice, key="k"), 409, "REQUEST_IN_PROGRESS")
    assert len(gateway.calls) == 0


def test_abandoned_pending_attempt_is_recovered(api_as, desk, issued_invoice, gateway):
    """The server crashed mid-call long ago: a retry takes over and finishes the job."""
    from datetime import timedelta

    from django.utils import timezone

    attempt = PaymentAttempt.objects.create(
        invoice=issued_invoice, amount_paise=236_000, idempotency_key="k", reference_id="cp_old"
    )
    PaymentAttempt.objects.filter(pk=attempt.pk).update(
        updated_at=timezone.now() - timedelta(minutes=5)
    )
    existing = rzp.link_response(reference_id="cp_old", amount=236_000)
    gateway.add(responses.GET, f"{RZP}/payment_links", json={"payment_links": [existing]})

    resp = collect_via_api(api_as(desk), issued_invoice, key="k")
    assert resp.status_code == 200
    assert resp.json()["gateway_link_id"] == existing["id"]
