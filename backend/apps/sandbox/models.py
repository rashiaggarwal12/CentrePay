"""State of the fake payment gateway (PAYMENT_GATEWAY=fake). This plays the role of
Razorpay's own database: our real models never read these tables, only the
FakeGateway client does, the same way it would call Razorpay's API."""

from django.db import models


class FakeLink(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    reference_id = models.CharField(max_length=40, unique=True)
    amount = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, default="created")  # created|paid|expired|cancelled
    description = models.CharField(max_length=2048, blank=True)
    customer = models.JSONField(default=dict)
    notes = models.JSONField(default=dict)
    expire_by = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} ({self.status})"


class FakePayment(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    link = models.ForeignKey(FakeLink, null=True, on_delete=models.CASCADE, related_name="payments")
    order_id = models.CharField(max_length=40)
    amount = models.PositiveBigIntegerField()
    amount_refunded = models.PositiveBigIntegerField(default=0)
    method = models.CharField(max_length=20)
    status = models.CharField(max_length=20)  # captured|failed|refunded
    fee = models.PositiveBigIntegerField(default=0)
    tax = models.PositiveBigIntegerField(default=0)
    notes = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)
    error_description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField()

    def __str__(self):
        return f"{self.id} {self.amount} ({self.status})"


class FakeRefund(models.Model):
    id = models.CharField(primary_key=True, max_length=40)
    payment = models.ForeignKey(FakePayment, on_delete=models.CASCADE, related_name="refunds")
    amount = models.PositiveBigIntegerField()
    receipt = models.CharField(max_length=64, blank=True)
    notes = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="processed")
    created_at = models.DateTimeField()

    def __str__(self):
        return f"{self.id} {self.amount} ({self.status})"
