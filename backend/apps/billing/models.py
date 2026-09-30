from django.db import models
from django.db.models import F, Q

from apps.accounts.models import Centre, Staff
from apps.customers.models import Customer


class Service(models.Model):
    """A billable item in a centre's catalogue (e.g. '45-min physiotherapy session')."""

    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="services")
    name = models.CharField(max_length=120)
    price_paise = models.PositiveBigIntegerField()
    gst_rate_bps = models.PositiveIntegerField(help_text="GST in basis points: 1800 = 18%")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["centre", "name"], name="uniq_service_name_per_centre"),
            models.CheckConstraint(
                condition=Q(gst_rate_bps__lte=10_000), name="service_gst_rate_at_most_100pct"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.centre.code})"


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
    REFUNDED = "refunded", "Refunded"
    CANCELLED = "cancelled", "Cancelled"


class Invoice(models.Model):
    """Status changes go through billing.state_machine; never assign `status` directly."""

    Status = InvoiceStatus

    # Assigned on issue, not on create: GST invoice numbers must be sequential with no gaps,
    # and abandoned drafts would leave gaps. Unique constraints ignore NULLs, so drafts are fine.
    number = models.CharField(max_length=16, unique=True, null=True, blank=True)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT, db_index=True
    )

    subtotal_paise = models.PositiveBigIntegerField(default=0)
    discount_paise = models.PositiveBigIntegerField(default=0)
    tax_paise = models.PositiveBigIntegerField(default=0)
    total_paise = models.PositiveBigIntegerField(default=0)
    amount_paid_paise = models.PositiveBigIntegerField(default=0)
    amount_refunded_paise = models.PositiveBigIntegerField(default=0)

    created_by = models.ForeignKey(Staff, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    # Bumped on every change. Clients send it back on edit to detect lost updates.
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["centre", "status", "created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(discount_paise__lte=F("subtotal_paise")),
                name="invoice_discount_within_subtotal",
            ),
            models.CheckConstraint(
                condition=Q(total_paise=F("subtotal_paise") - F("discount_paise") + F("tax_paise")),
                name="invoice_total_consistent",
            ),
            models.CheckConstraint(
                condition=Q(amount_refunded_paise__lte=F("amount_paid_paise")),
                name="invoice_refunds_within_paid",
            ),
        ]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"

    @property
    def amount_due_paise(self) -> int:
        return max(self.total_paise - self.amount_paid_paise, 0)


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255)
    qty = models.PositiveIntegerField()
    # Price and GST rate are copied from the Service at billing time, so later
    # catalogue changes never alter an existing invoice.
    unit_price_paise = models.PositiveBigIntegerField()
    gst_rate_bps = models.PositiveIntegerField()
    line_total_paise = models.PositiveBigIntegerField(help_text="qty x unit price, before discount")
    discount_paise = models.PositiveBigIntegerField(
        default=0, help_text="Share of invoice discount"
    )
    tax_paise = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(qty__gte=1), name="invoice_item_qty_positive"),
            models.CheckConstraint(
                condition=Q(line_total_paise=F("qty") * F("unit_price_paise")),
                name="invoice_item_line_total_consistent",
            ),
        ]

    def __str__(self):
        return f"{self.qty} x {self.description}"


class InvoiceCounter(models.Model):
    """Per-centre, per-financial-year sequence for invoice numbers. Row-locked on use."""

    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="+")
    fiscal_year = models.PositiveSmallIntegerField(
        help_text="Starting year, e.g. 2026 for FY 26-27"
    )
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "fiscal_year"], name="uniq_counter_per_centre_fy"
            ),
        ]

    def __str__(self):
        return f"{self.centre_id}/FY{self.fiscal_year}: {self.last_value}"
