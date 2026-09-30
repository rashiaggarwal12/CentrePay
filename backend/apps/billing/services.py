"""Billing business logic. Views stay thin and call these functions.

Every mutation:
  * runs in a transaction,
  * locks the invoice row (select_for_update) so concurrent requests serialize,
  * changes status only via the state machine,
  * bumps `version` and writes an audit entry in the same transaction.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.audit.services import audit, snapshot
from apps.common.exceptions import (
    BusinessValidationError,
    InvalidTransition,
    InvoiceNotEditable,
    ManagerRequired,
    StaleVersion,
)
from apps.common.money import allocate, percent_of

from . import state_machine
from .models import Invoice, InvoiceCounter, InvoiceItem, InvoiceStatus, Service
from .state_machine import Event

INVOICE_FIELDS = [
    "number",
    "status",
    "customer",
    "subtotal_paise",
    "discount_paise",
    "tax_paise",
    "total_paise",
    "amount_paid_paise",
    "amount_refunded_paise",
    "issued_at",
    "cancelled_at",
    "cancel_reason",
    "version",
]


# --- Totals -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LineInput:
    service: Service
    qty: int
    description: str = ""


@dataclass(frozen=True)
class LineTotals:
    line_total_paise: int
    discount_paise: int
    tax_paise: int


@dataclass(frozen=True)
class Totals:
    lines: list[LineTotals]
    subtotal_paise: int
    discount_paise: int
    tax_paise: int
    total_paise: int


def compute_totals(lines: list[tuple[int, int, int]], discount_paise: int) -> Totals:
    """Pure function: [(qty, unit_price_paise, gst_rate_bps), ...] + invoice discount -> totals.

    GST is charged on the discounted value, so the invoice-level discount is first
    spread across lines in proportion to their value, then each line is taxed at its
    own rate. Rounding happens per line (half-up), which is how the tax appears on
    the printed invoice.
    """
    line_totals = [qty * unit for qty, unit, _ in lines]
    subtotal = sum(line_totals)
    if discount_paise < 0:
        raise BusinessValidationError("Discount cannot be negative.")
    if discount_paise > subtotal:
        raise BusinessValidationError("Discount cannot exceed the subtotal.")

    line_discounts = allocate(discount_paise, line_totals)
    result_lines = []
    for (_, _, gst_bps), line_total, line_discount in zip(
        lines, line_totals, line_discounts, strict=True
    ):
        tax = percent_of(line_total - line_discount, gst_bps)
        result_lines.append(LineTotals(line_total, line_discount, tax))

    tax_total = sum(line.tax_paise for line in result_lines)
    return Totals(
        lines=result_lines,
        subtotal_paise=subtotal,
        discount_paise=discount_paise,
        tax_paise=tax_total,
        total_paise=subtotal - discount_paise + tax_total,
    )


def _write_items(invoice: Invoice, lines: list[LineInput], discount_paise: int) -> None:
    """Replace the invoice's items and recompute its totals. Caller holds the lock."""
    if not lines:
        raise BusinessValidationError("An invoice needs at least one item.")
    for line in lines:
        if line.service.centre_id != invoice.centre_id:
            raise BusinessValidationError("Service belongs to a different centre.")
        if not line.service.is_active:
            raise BusinessValidationError(f"Service '{line.service.name}' is no longer offered.")

    totals = compute_totals(
        [(line.qty, line.service.price_paise, line.service.gst_rate_bps) for line in lines],
        discount_paise,
    )
    invoice.items.all().delete()
    InvoiceItem.objects.bulk_create(
        InvoiceItem(
            invoice=invoice,
            service=line.service,
            description=line.description or line.service.name,
            qty=line.qty,
            unit_price_paise=line.service.price_paise,
            gst_rate_bps=line.service.gst_rate_bps,
            line_total_paise=lt.line_total_paise,
            discount_paise=lt.discount_paise,
            tax_paise=lt.tax_paise,
        )
        for line, lt in zip(lines, totals.lines, strict=True)
    )
    invoice.subtotal_paise = totals.subtotal_paise
    invoice.discount_paise = totals.discount_paise
    invoice.tax_paise = totals.tax_paise
    invoice.total_paise = totals.total_paise


# --- Helpers ----------------------------------------------------------------------------------


def _lock(invoice_id: int) -> Invoice:
    return Invoice.objects.select_for_update().get(pk=invoice_id)


def _check_version(invoice: Invoice, expected_version: int | None) -> None:
    if expected_version is not None and expected_version != invoice.version:
        raise StaleVersion()


def fiscal_year_start(when) -> int:
    """Indian financial year runs April to March."""
    local = timezone.localtime(when)
    return local.year if local.month >= 4 else local.year - 1


def next_invoice_number(centre, when) -> str:
    """Gap-free, per-centre, per-FY sequence, e.g. BLR1/2627/000042.

    The counter row is locked, so two invoices issued at the same moment get
    consecutive numbers rather than the same one.
    """
    fy = fiscal_year_start(when)
    counter, _ = InvoiceCounter.objects.select_for_update().get_or_create(
        centre=centre, fiscal_year=fy
    )
    counter.last_value += 1
    counter.save(update_fields=["last_value"])
    return f"{centre.code}/{fy % 100:02d}{(fy + 1) % 100:02d}/{counter.last_value:06d}"


# --- Commands ---------------------------------------------------------------------------------


@transaction.atomic
def create_draft(*, staff, customer, lines: list[LineInput], discount_paise: int = 0) -> Invoice:
    if customer.centre_id != staff.centre_id:
        raise BusinessValidationError("Customer belongs to a different centre.")
    invoice = Invoice.objects.create(centre=staff.centre, customer=customer, created_by=staff)
    _write_items(invoice, lines, discount_paise)
    invoice.save()
    audit("invoice.created", invoice, actor=staff.user, after=snapshot(invoice, INVOICE_FIELDS))
    return invoice


@transaction.atomic
def update_draft(
    invoice_id: int,
    *,
    staff,
    customer=None,
    lines: list[LineInput] | None = None,
    discount_paise: int | None = None,
    expected_version: int | None = None,
) -> Invoice:
    invoice = _lock(invoice_id)
    _check_version(invoice, expected_version)
    if invoice.status not in state_machine.EDITABLE_STATUSES:
        raise InvoiceNotEditable()
    before = snapshot(invoice, INVOICE_FIELDS)

    if customer is not None:
        if customer.centre_id != invoice.centre_id:
            raise BusinessValidationError("Customer belongs to a different centre.")
        invoice.customer = customer

    if lines is None and discount_paise is not None:
        # Discount-only change: re-price the existing lines with the new discount.
        lines = [
            LineInput(item.service, item.qty, item.description) for item in invoice.items.all()
        ]
    if lines is not None:
        _write_items(
            invoice, lines, invoice.discount_paise if discount_paise is None else discount_paise
        )

    invoice.version += 1
    invoice.save()
    audit(
        "invoice.updated",
        invoice,
        actor=staff.user,
        before=before,
        after=snapshot(invoice, INVOICE_FIELDS),
    )
    return invoice


@transaction.atomic
def issue_invoice(invoice_id: int, *, staff, expected_version: int | None = None) -> Invoice:
    invoice = _lock(invoice_id)
    _check_version(invoice, expected_version)
    before = snapshot(invoice, INVOICE_FIELDS)

    state_machine.apply(invoice, Event.ISSUE)
    if invoice.total_paise <= 0:
        raise BusinessValidationError("Cannot issue an invoice with a zero total.")

    invoice.issued_at = timezone.now()
    invoice.number = next_invoice_number(invoice.centre, invoice.issued_at)
    invoice.version += 1
    invoice.save()
    audit(
        "invoice.issued",
        invoice,
        actor=staff.user,
        before=before,
        after=snapshot(invoice, INVOICE_FIELDS),
    )
    return invoice


@transaction.atomic
def cancel_invoice(
    invoice_id: int, *, staff, reason: str, expected_version: int | None = None
) -> Invoice:
    invoice = _lock(invoice_id)
    _check_version(invoice, expected_version)
    before = snapshot(invoice, INVOICE_FIELDS)

    if invoice.amount_paid_paise > 0:
        # Belt and braces: the state machine already forbids cancelling once paid.
        raise InvalidTransition("Cannot cancel an invoice with payments. Refund it instead.")
    if invoice.status == InvoiceStatus.ISSUED and not staff.is_manager:
        # An issued invoice is a legal document; voiding one needs a manager.
        raise ManagerRequired("Only a manager can cancel an issued invoice.")

    state_machine.apply(invoice, Event.CANCEL)
    invoice.cancelled_at = timezone.now()
    invoice.cancel_reason = reason
    invoice.version += 1
    invoice.save()
    audit(
        "invoice.cancelled",
        invoice,
        actor=staff.user,
        before=before,
        after=snapshot(invoice, INVOICE_FIELDS),
    )
    if invoice.payment_attempts.filter(status="created").exists():
        # Kill live payment links so the customer can't pay a cancelled invoice.
        from apps.payments.tasks import cancel_all_links  # payments depends on billing

        transaction.on_commit(lambda: cancel_all_links.delay(invoice.pk))
    return invoice
