from django.db import models
from django.db.models import Q


class PaymentAttempt(models.Model):
    """One 'collect' request: a Razorpay payment link (shown as a QR) for some amount.

    Created in our DB *before* calling the gateway, so a crash mid-call leaves a
    `pending` row we can recover from instead of an orphaned link we don't know about.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending (gateway not called yet)"
        CREATED = "created", "Link active"
        PAID = "paid", "Paid"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Gateway call failed"

    ACTIVE_STATUSES = (Status.PENDING, Status.CREATED)

    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="payment_attempts"
    )
    amount_paise = models.PositiveBigIntegerField()
    idempotency_key = models.CharField(max_length=64, unique=True)
    # Sent to Razorpay as the link's reference_id (unique per Razorpay account). Lets us
    # find the link again if we crashed after Razorpay created it but before we saved its id.
    reference_id = models.CharField(max_length=40, unique=True)
    gateway_link_id = models.CharField(max_length=40, unique=True, null=True, blank=True)
    short_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.gateway_link_id or self.reference_id} ({self.status})"


class Payment(models.Model):
    """Money the gateway says the customer paid. Keyed by Razorpay's payment id."""

    class Status(models.TextChoices):
        AUTHORIZED = "authorized", "Authorized"
        CAPTURED = "captured", "Captured"
        FAILED = "failed", "Failed"

    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="payments"
    )
    attempt = models.ForeignKey(
        PaymentAttempt, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    gateway_payment_id = models.CharField(max_length=40, unique=True)
    gateway_order_id = models.CharField(max_length=40, blank=True)
    amount_paise = models.PositiveBigIntegerField()
    method = models.CharField(max_length=20, blank=True, help_text="upi, card, netbanking, ...")
    status = models.CharField(max_length=20, choices=Status.choices)
    fee_paise = models.PositiveBigIntegerField(default=0, help_text="Gateway fee incl. GST")
    captured_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="captured") | Q(captured_at__isnull=False),
                name="payment_captured_has_timestamp",
            ),
        ]

    def __str__(self):
        return f"{self.gateway_payment_id} {self.amount_paise} ({self.status})"
