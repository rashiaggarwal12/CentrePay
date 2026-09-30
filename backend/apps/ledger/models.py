from django.db import models
from django.db.models import Q


class LedgerEntry(models.Model):
    """Append-only money movements per invoice. Corrections are new entries, never edits.

    Credits are money received from the customer; debits are money returned (refunds).
    For any invoice: sum(credit) - sum(debit) == amount_paid - amount_refunded.
    """

    class EntryType(models.TextChoices):
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"

    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.PROTECT, related_name="ledger_entries"
    )
    payment = models.ForeignKey(
        "payments.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    debit_paise = models.PositiveBigIntegerField(default=0)
    credit_paise = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "ledger entries"
        constraints = [
            # Exactly one side is non-zero.
            models.CheckConstraint(
                condition=(Q(debit_paise=0) & Q(credit_paise__gt=0))
                | (Q(credit_paise=0) & Q(debit_paise__gt=0)),
                name="ledger_one_sided_entry",
            ),
            # One payment is credited to the ledger at most once, whatever the retries.
            models.UniqueConstraint(
                fields=["payment", "entry_type"],
                condition=Q(entry_type="payment"),
                name="uniq_ledger_credit_per_payment",
            ),
        ]

    def __str__(self):
        side = f"+{self.credit_paise}" if self.credit_paise else f"-{self.debit_paise}"
        return f"{self.entry_type} {side} (invoice {self.invoice_id})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("LedgerEntry is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("LedgerEntry is append-only")
