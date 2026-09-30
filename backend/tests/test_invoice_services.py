from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus
from apps.common.exceptions import (
    BusinessValidationError,
    InvalidTransition,
    InvoiceNotEditable,
    ManagerRequired,
    StaleVersion,
)

from .factories import CustomerFactory, ServiceFactory

IST = ZoneInfo("Asia/Kolkata")
pytestmark = pytest.mark.django_db


# --- Totals ---------------------------------------------------------------------------------


def test_compute_totals_single_line():
    t = billing.compute_totals([(2, 100_000, 1800)], discount_paise=0)
    assert (t.subtotal_paise, t.discount_paise, t.tax_paise, t.total_paise) == (
        200_000,
        0,
        36_000,
        236_000,
    )


def test_compute_totals_discount_is_spread_before_tax():
    # ₹2000 @ 18% + ₹1200 @ 0%, ₹320 discount -> ₹200 on the taxed line, ₹120 on the exempt one.
    t = billing.compute_totals([(1, 200_000, 1800), (1, 120_000, 0)], discount_paise=32_000)
    assert [line.discount_paise for line in t.lines] == [20_000, 12_000]
    assert [line.tax_paise for line in t.lines] == [32_400, 0]  # 18% of ₹1800
    assert t.total_paise == 320_000 - 32_000 + 32_400


def test_compute_totals_rounds_per_line():
    # ₹0.03 @ 18% = 0.54 paise -> 1 paisa on each of 3 lines, 3 total (not round(1.62) = 2)
    t = billing.compute_totals([(1, 3, 1800)] * 3, discount_paise=0)
    assert t.tax_paise == 3


def test_compute_totals_rejects_excess_discount():
    with pytest.raises(BusinessValidationError):
        billing.compute_totals([(1, 100, 0)], discount_paise=101)


# --- Drafts ---------------------------------------------------------------------------------


def test_create_draft_snapshots_prices(desk, customer, service, exempt_service):
    invoice = billing.create_draft(
        staff=desk,
        customer=customer,
        lines=[billing.LineInput(service, 1), billing.LineInput(exempt_service, 2, "Knee rehab")],
        discount_paise=10_000,
    )
    assert invoice.status == InvoiceStatus.DRAFT
    assert invoice.number is None
    assert invoice.subtotal_paise == 200_000 + 240_000
    assert invoice.total_paise == invoice.subtotal_paise - 10_000 + invoice.tax_paise
    items = list(invoice.items.all())
    assert [i.description for i in items] == ["Sports massage", "Knee rehab"]

    service.price_paise = 999_999
    service.save()
    invoice.refresh_from_db()
    assert invoice.items.first().unit_price_paise == 200_000  # catalogue change doesn't leak in


def test_create_draft_writes_audit(desk, make_draft):
    invoice = make_draft()
    log = AuditLog.objects.get(action="invoice.created")
    assert log.entity_id == str(invoice.pk)
    assert log.actor == desk.user
    assert log.after["total_paise"] == invoice.total_paise


def test_create_draft_rejects_other_centres_data(desk, customer, other_centre):
    foreign_service = ServiceFactory(centre=other_centre)
    with pytest.raises(BusinessValidationError, match="different centre"):
        billing.create_draft(
            staff=desk, customer=customer, lines=[billing.LineInput(foreign_service, 1)]
        )

    foreign_customer = CustomerFactory(centre=other_centre)
    local_service = ServiceFactory(centre=desk.centre)
    with pytest.raises(BusinessValidationError, match="different centre"):
        billing.create_draft(
            staff=desk, customer=foreign_customer, lines=[billing.LineInput(local_service, 1)]
        )


def test_create_draft_rejects_inactive_service(desk, customer, service):
    service.is_active = False
    service.save()
    with pytest.raises(BusinessValidationError, match="no longer offered"):
        billing.create_draft(staff=desk, customer=customer, lines=[billing.LineInput(service, 1)])


def test_update_draft_replaces_items_and_bumps_version(desk, make_draft, exempt_service):
    invoice = make_draft(qty=1)
    updated = billing.update_draft(
        invoice.pk, staff=desk, lines=[billing.LineInput(exempt_service, 3)], expected_version=1
    )
    assert updated.version == 2
    assert updated.items.count() == 1
    assert updated.total_paise == 360_000


def test_update_draft_discount_only_reprices(desk, make_draft):
    invoice = make_draft(qty=1)  # ₹2000 @ 18%
    updated = billing.update_draft(invoice.pk, staff=desk, discount_paise=50_000)
    assert updated.discount_paise == 50_000
    assert updated.tax_paise == 27_000  # 18% of ₹1500
    assert updated.total_paise == 177_000


def test_update_draft_rejects_stale_version(desk, make_draft):
    invoice = make_draft()
    billing.update_draft(invoice.pk, staff=desk, discount_paise=100, expected_version=1)
    with pytest.raises(StaleVersion):
        billing.update_draft(invoice.pk, staff=desk, discount_paise=200, expected_version=1)


def test_issued_invoice_is_immutable(desk, make_draft):
    invoice = make_draft()
    billing.issue_invoice(invoice.pk, staff=desk)
    with pytest.raises(InvoiceNotEditable):
        billing.update_draft(invoice.pk, staff=desk, discount_paise=100)


# --- Issue ----------------------------------------------------------------------------------


def test_issue_assigns_sequential_numbers(desk, make_draft):
    first = billing.issue_invoice(make_draft().pk, staff=desk)
    second = billing.issue_invoice(make_draft().pk, staff=desk)
    assert first.status == InvoiceStatus.ISSUED
    assert first.issued_at is not None
    assert first.number.startswith("BLR1/")
    assert int(second.number.rsplit("/", 1)[1]) == int(first.number.rsplit("/", 1)[1]) + 1
    assert len(first.number) <= 16  # GST limit


def test_numbers_are_per_centre(desk, make_draft, other_centre):
    from .factories import StaffFactory

    other_desk = StaffFactory(centre=other_centre)
    other_customer = CustomerFactory(centre=other_centre)
    other_service = ServiceFactory(centre=other_centre)

    a = billing.issue_invoice(make_draft().pk, staff=desk)
    b = billing.issue_invoice(
        make_draft(staff=other_desk, cust=other_customer, svc=other_service).pk, staff=other_desk
    )
    assert a.number.endswith("/000001")
    assert b.number.startswith("MUM1/") and b.number.endswith("/000001")


@pytest.mark.parametrize(
    ("when", "fy_label"),
    [
        (datetime(2026, 3, 31, 23, 59, tzinfo=IST), "2526"),
        (datetime(2026, 4, 1, 0, 1, tzinfo=IST), "2627"),
        # 20:00 UTC on 31 Mar is already 1 Apr in India
        (datetime(2026, 3, 31, 20, 0, tzinfo=ZoneInfo("UTC")), "2627"),
    ],
)
def test_invoice_number_uses_indian_financial_year(centre, when, fy_label):
    assert billing.next_invoice_number(centre, when) == f"BLR1/{fy_label}/000001"


def test_cannot_issue_twice(desk, make_draft):
    invoice = make_draft()
    billing.issue_invoice(invoice.pk, staff=desk)
    with pytest.raises(InvalidTransition):
        billing.issue_invoice(invoice.pk, staff=desk)


def test_cannot_issue_zero_total(desk, make_draft):
    invoice = make_draft(discount_paise=200_000)  # 100% discount
    with pytest.raises(BusinessValidationError, match="zero total"):
        billing.issue_invoice(invoice.pk, staff=desk)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.DRAFT  # transaction rolled back


# --- Cancel ---------------------------------------------------------------------------------


def test_front_desk_can_cancel_draft(desk, make_draft):
    invoice = billing.cancel_invoice(make_draft().pk, staff=desk, reason="Wrong customer")
    assert invoice.status == InvoiceStatus.CANCELLED
    assert invoice.cancel_reason == "Wrong customer"
    assert invoice.cancelled_at is not None


def test_only_manager_can_cancel_issued(desk, manager, make_draft):
    invoice = billing.issue_invoice(make_draft().pk, staff=desk)
    with pytest.raises(ManagerRequired):
        billing.cancel_invoice(invoice.pk, staff=desk, reason="oops")
    cancelled = billing.cancel_invoice(invoice.pk, staff=manager, reason="Customer left")
    assert cancelled.status == InvoiceStatus.CANCELLED
    assert AuditLog.objects.filter(action="invoice.cancelled", actor=manager.user).exists()


def test_cannot_cancel_once_money_received(desk, manager, make_draft):
    invoice = billing.issue_invoice(make_draft().pk, staff=desk)
    Invoice.objects.filter(pk=invoice.pk).update(
        amount_paid_paise=1_000, status=InvoiceStatus.PARTIALLY_PAID
    )
    with pytest.raises(InvalidTransition):
        billing.cancel_invoice(invoice.pk, staff=manager, reason="x")


# --- Database guards ------------------------------------------------------------------------


def test_db_rejects_inconsistent_total(make_draft):
    invoice = make_draft()
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.filter(pk=invoice.pk).update(total_paise=1)


def test_db_rejects_refund_above_paid(make_draft):
    invoice = make_draft()
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.filter(pk=invoice.pk).update(amount_refunded_paise=1)
