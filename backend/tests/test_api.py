import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus

from .factories import CentreFactory, CustomerFactory, ServiceFactory, StaffFactory, UserFactory

pytestmark = pytest.mark.django_db


def assert_error(response, status_code, code):
    assert response.status_code == status_code, response.content
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body["error"]


# --- Auth -----------------------------------------------------------------------------------


def test_login_returns_tokens_and_me_works(api, desk):
    resp = api.post(
        "/api/v1/auth/token/", {"username": desk.user.username, "password": "pass12345"}
    )
    assert resp.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.json()['access']}")
    me = api.get("/api/v1/auth/me/").json()
    assert me["role"] == "front_desk"
    assert me["centre"]["code"] == "BLR1"


def test_bad_login_uses_error_format(api, desk):
    resp = api.post("/api/v1/auth/token/", {"username": desk.user.username, "password": "nope"})
    assert_error(resp, 401, "AUTHENTICATION_FAILED")


def test_login_is_rate_limited(api, desk):
    cache.clear()
    for _ in range(10):
        api.post("/api/v1/auth/token/", {"username": desk.user.username, "password": "nope"})
    resp = api.post("/api/v1/auth/token/", {"username": desk.user.username, "password": "nope"})
    assert_error(resp, 429, "RATE_LIMITED")
    cache.clear()


def test_unauthenticated_is_rejected(api):
    assert_error(api.get("/api/v1/invoices/"), 401, "NOT_AUTHENTICATED")


def test_user_without_staff_profile_is_rejected(api_as, db):
    client = APIClient()
    client.force_authenticate(UserFactory())
    assert_error(client.get("/api/v1/invoices/"), 403, "PERMISSION_DENIED")


def test_inactive_centre_is_rejected(api_as, desk):
    desk.centre.is_active = False
    desk.centre.save()
    assert_error(api_as(desk).get("/api/v1/invoices/"), 403, "PERMISSION_DENIED")


# --- Customers ------------------------------------------------------------------------------


def test_create_customer_normalizes_phone(api_as, desk):
    resp = api_as(desk).post("/api/v1/customers/", {"name": "Priya", "phone": "098123 45678"})
    assert resp.status_code == 201, resp.content
    assert resp.json()["phone"] == "+919812345678"


def test_duplicate_phone_in_centre_rejected(api_as, desk, customer):
    resp = api_as(desk).post("/api/v1/customers/", {"name": "Dup", "phone": customer.phone})
    err = assert_error(resp, 400, "VALIDATION_ERROR")
    assert "phone" in err["details"]


def test_same_phone_allowed_in_another_centre(api_as, desk, other_centre):
    CustomerFactory(centre=other_centre, phone="+919812345678")
    resp = api_as(desk).post("/api/v1/customers/", {"name": "Priya", "phone": "9812345678"})
    assert resp.status_code == 201


def test_invalid_phone_rejected(api_as, desk):
    resp = api_as(desk).post("/api/v1/customers/", {"name": "X", "phone": "12345"})
    assert_error(resp, 400, "VALIDATION_ERROR")


def test_customer_search_is_centre_scoped(api_as, desk, other_centre):
    CustomerFactory(centre=desk.centre, name="Rohan Local")
    CustomerFactory(centre=other_centre, name="Rohan Elsewhere")
    results = api_as(desk).get("/api/v1/customers/?search=rohan").json()["results"]
    assert [c["name"] for c in results] == ["Rohan Local"]


# --- Services -------------------------------------------------------------------------------


def test_services_lists_only_active_in_centre(api_as, desk, service, other_centre):
    ServiceFactory(centre=desk.centre, is_active=False)
    ServiceFactory(centre=other_centre)
    data = api_as(desk).get("/api/v1/services/").json()
    assert [s["id"] for s in data] == [service.id]


# --- Invoices -------------------------------------------------------------------------------


def test_create_invoice_computes_totals_server_side(api_as, desk, customer, service):
    resp = api_as(desk).post(
        "/api/v1/invoices/",
        {
            "customer": customer.id,
            "items": [{"service": service.id, "qty": 2}],
            "discount_paise": 0,
            "total_paise": 1,  # ignored: clients can't set totals
            "status": "paid",  # ignored: clients can't set status
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["status"] == "draft"
    assert body["total_paise"] == 472_000
    assert body["items"][0]["unit_price_paise"] == 200_000


def test_create_invoice_with_other_centres_service_is_rejected(
    api_as, desk, customer, other_centre
):
    foreign = ServiceFactory(centre=other_centre)
    resp = api_as(desk).post(
        "/api/v1/invoices/",
        {"customer": customer.id, "items": [{"service": foreign.id, "qty": 1}]},
        format="json",
    )
    err = assert_error(resp, 400, "VALIDATION_ERROR")
    assert "items" in err["details"]


def test_create_invoice_requires_items(api_as, desk, customer):
    resp = api_as(desk).post(
        "/api/v1/invoices/", {"customer": customer.id, "items": []}, format="json"
    )
    assert_error(resp, 400, "VALIDATION_ERROR")


def test_patch_draft(api_as, desk, make_draft, exempt_service):
    invoice = make_draft()
    resp = api_as(desk).patch(
        f"/api/v1/invoices/{invoice.id}/",
        {"items": [{"service": exempt_service.id, "qty": 1}], "version": 1},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["total_paise"] == 120_000
    assert resp.json()["version"] == 2


def test_patch_with_stale_version_conflicts(api_as, desk, make_draft):
    invoice = make_draft()
    client = api_as(desk)
    client.patch(
        f"/api/v1/invoices/{invoice.id}/", {"discount_paise": 100, "version": 1}, format="json"
    )
    resp = client.patch(
        f"/api/v1/invoices/{invoice.id}/", {"discount_paise": 200, "version": 1}, format="json"
    )
    assert_error(resp, 409, "STALE_VERSION")


def test_patch_issued_invoice_is_rejected(api_as, desk, make_draft):
    invoice = billing.issue_invoice(make_draft().pk, staff=desk)
    resp = api_as(desk).patch(
        f"/api/v1/invoices/{invoice.id}/", {"discount_paise": 1}, format="json"
    )
    assert_error(resp, 409, "INVOICE_NOT_EDITABLE")


def test_put_is_not_allowed(api_as, desk, make_draft):
    invoice = make_draft()
    resp = api_as(desk).put(f"/api/v1/invoices/{invoice.id}/", {}, format="json")
    assert_error(resp, 405, "METHOD_NOT_ALLOWED")


def test_issue_and_cancel_via_api(api_as, desk, manager, make_draft):
    invoice = make_draft()
    resp = api_as(desk).post(f"/api/v1/invoices/{invoice.id}/issue/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "issued"
    assert resp.json()["number"].startswith("BLR1/")

    resp = api_as(desk).post(f"/api/v1/invoices/{invoice.id}/cancel/", {"reason": "x"})
    assert_error(resp, 403, "MANAGER_REQUIRED")

    resp = api_as(manager).post(f"/api/v1/invoices/{invoice.id}/cancel/", {"reason": "Walked out"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_invalid_transition_error_format(api_as, desk, make_draft):
    invoice = make_draft()
    billing.cancel_invoice(invoice.pk, staff=desk, reason="x")
    resp = api_as(desk).post(f"/api/v1/invoices/{invoice.id}/issue/")
    assert_error(resp, 409, "INVALID_TRANSITION")


def test_cancel_requires_reason(api_as, desk, make_draft):
    invoice = make_draft()
    resp = api_as(desk).post(f"/api/v1/invoices/{invoice.id}/cancel/", {})
    assert_error(resp, 400, "VALIDATION_ERROR")


def test_other_centres_invoice_is_404(api_as, make_draft):
    invoice = make_draft()
    intruder = StaffFactory(centre=CentreFactory(), role="manager")
    client = api_as(intruder)
    assert_error(client.get(f"/api/v1/invoices/{invoice.id}/"), 404, "NOT_FOUND")
    assert_error(client.post(f"/api/v1/invoices/{invoice.id}/issue/"), 404, "NOT_FOUND")
    assert_error(
        client.patch(f"/api/v1/invoices/{invoice.id}/", {"discount_paise": 1}, format="json"),
        404,
        "NOT_FOUND",
    )
    assert client.get("/api/v1/invoices/").json()["results"] == []
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.DRAFT


def test_list_filters(api_as, desk, make_draft):
    draft = make_draft()
    issued = billing.issue_invoice(make_draft().pk, staff=desk)
    client = api_as(desk)

    ids = {r["id"] for r in client.get("/api/v1/invoices/?status=issued").json()["results"]}
    assert ids == {issued.id}

    today = timezone.localdate().isoformat()
    ids = {r["id"] for r in client.get(f"/api/v1/invoices/?date={today}").json()["results"]}
    assert ids == {draft.id, issued.id}
    assert client.get("/api/v1/invoices/?date=2000-01-01").json()["results"] == []


def test_detail_includes_items(api_as, desk, make_draft):
    invoice = make_draft(qty=3)
    body = api_as(desk).get(f"/api/v1/invoices/{invoice.id}/").json()
    assert body["items"][0]["qty"] == 3
    assert body["amount_due_paise"] == Invoice.objects.get(pk=invoice.pk).total_paise


# --- Misc -----------------------------------------------------------------------------------


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_seed_command_is_idempotent(db):
    from django.core.management import call_command

    call_command("seed")
    call_command("seed")
    assert Invoice.objects.filter(status=InvoiceStatus.ISSUED).count() == 3
    assert Invoice.objects.filter(status=InvoiceStatus.DRAFT).count() == 3
