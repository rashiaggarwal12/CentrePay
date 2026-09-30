from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q


class ReconciliationRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    date = models.DateField(help_text="The business day being reconciled (IST)")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    summary = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"Reconciliation {self.date} ({self.status})"


class ReconciliationIssue(models.Model):
    """Something a human needs to look at. Raised by the daily run, and also inline by
    payment processing (e.g. an overpayment) so problems surface the moment they happen."""

    class Kind(models.TextChoices):
        OVERPAID = "overpaid", "Invoice overpaid"
        AMOUNT_MISMATCH = "amount_mismatch", "Amount mismatch"
        PAYMENT_ON_CLOSED_INVOICE = "payment_on_closed_invoice", "Payment on closed invoice"
        UNMATCHED_PAYMENT = "unmatched_payment", "Payment not linked to an invoice"
        MISSING_LOCALLY = "missing_locally", "Captured at gateway, missing locally"
        MISSING_AT_GATEWAY = "missing_at_gateway", "Recorded locally, missing at gateway"
        STATUS_MISMATCH = "status_mismatch", "Status mismatch"
        INVOICE_DRIFT = "invoice_drift", "Invoice paid amount drift"
        SETTLEMENT_MISMATCH = "settlement_mismatch", "Settlement mismatch"
        STUCK_EVENT = "stuck_event", "Webhook event stuck"
        REFUND_ORPHANED = "refund_orphaned", "Refund for unknown payment"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    run = models.ForeignKey(
        ReconciliationRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="issues"
    )  # null = raised inline, outside a daily run
    kind = models.CharField(max_length=40, choices=Kind.choices)
    gateway_ref = models.CharField(max_length=64, help_text="e.g. pay_XXXX, plink_XXXX, event id")
    local_ref = models.CharField(max_length=64, blank=True)
    invoice = models.ForeignKey(
        "billing.Invoice", null=True, blank=True, on_delete=models.PROTECT, related_name="issues"
    )
    expected_paise = models.BigIntegerField(null=True, blank=True)
    actual_paise = models.BigIntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Retries and re-runs must not pile up duplicates of the same open problem.
            models.UniqueConstraint(
                fields=["kind", "gateway_ref"],
                condition=Q(status="open"),
                name="uniq_open_issue_per_kind_ref",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.gateway_ref}"
