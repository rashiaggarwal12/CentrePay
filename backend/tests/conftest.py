import json
import re
import uuid

import pytest
import responses
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import Staff
from apps.billing import services as billing
from apps.payments import services as payments

from . import razorpay as rzp
from .factories import CentreFactory, CustomerFactory, ServiceFactory, StaffFactory

RZP = "https://api.razorpay.com/v1"


@pytest.fixture
def centre(db):
    return CentreFactory(code="BLR1")


@pytest.fixture
def other_centre(db):
    return CentreFactory(code="MUM1")


@pytest.fixture
def desk(centre):
    return StaffFactory(centre=centre, role=Staff.Role.FRONT_DESK)


@pytest.fixture
def manager(centre):
    return StaffFactory(centre=centre, role=Staff.Role.MANAGER)


@pytest.fixture
def customer(centre):
    return CustomerFactory(centre=centre)


@pytest.fixture
def service(centre):
    return ServiceFactory(
        centre=centre, name="Sports massage", price_paise=200_000, gst_rate_bps=1800
    )


@pytest.fixture
def exempt_service(centre):
    return ServiceFactory(centre=centre, name="Physio session", price_paise=120_000, gst_rate_bps=0)


@pytest.fixture
def api():
    """An API client; call api_as(staff) to authenticate."""
    return APIClient()


@pytest.fixture
def api_as():
    def _client(staff):
        client = APIClient()
        client.force_authenticate(user=staff.user)
        return client

    return _client


@pytest.fixture
def issued_invoice(make_draft, desk):
    """₹2,360.00 invoice (₹2,000 + 18% GST), issued."""
    return billing.issue_invoice(make_draft().pk, staff=desk)


@pytest.fixture
def gateway():
    """Intercepts every HTTP call to Razorpay. Unmocked calls fail the test."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def mock_link_creation(gateway):
    """Razorpay's create-link endpoint echoes back a link for whatever was requested."""

    def _callback(request):
        body = json.loads(request.body)
        link = rzp.link_response(reference_id=body["reference_id"], amount=body["amount"])
        return 200, {}, json.dumps(link)

    gateway.add_callback(responses.POST, f"{RZP}/payment_links", callback=_callback)
    gateway.add(responses.POST, re.compile(rf"{RZP}/payment_links/.+/cancel"), json={})
    return gateway


@pytest.fixture
def collect(desk, mock_link_creation):
    """Create a payment link through the real service (gateway mocked)."""

    def _collect(invoice, amount_paise=None, key=None, staff=None):
        attempt, _ = payments.collect(
            invoice.pk,
            staff=staff or desk,
            idempotency_key=key or uuid.uuid4().hex,
            amount_paise=amount_paise,
        )
        return attempt

    return _collect


@pytest.fixture
def deliver(client, django_capture_on_commit_callbacks):
    """POST a signed webhook, running on_commit callbacks (i.e. the Celery task) inline."""

    def _deliver(payload, *, event_id=None, secret=None, signature=None):
        body = rzp.encode(payload)
        headers = {
            "HTTP_X_RAZORPAY_SIGNATURE": signature
            if signature is not None
            else rzp.sign(body, secret or settings.RAZORPAY_WEBHOOK_SECRET),
            "HTTP_X_RAZORPAY_EVENT_ID": event_id or rzp.next_id("evt"),
        }
        with django_capture_on_commit_callbacks(execute=True):
            return client.post(
                "/webhooks/razorpay/", data=body, content_type="application/json", **headers
            )

    return _deliver


@pytest.fixture
def make_draft(desk, customer, service):
    def _make(qty=1, discount_paise=0, staff=None, cust=None, svc=None):
        return billing.create_draft(
            staff=staff or desk,
            customer=cust or customer,
            lines=[billing.LineInput(svc or service, qty)],
            discount_paise=discount_paise,
        )

    return _make
