from django.db import models

from apps.accounts.models import Centre


class Customer(models.Model):
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="customers")
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=15, help_text="E.164, e.g. +919812345678")
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "phone"], name="uniq_customer_phone_per_centre"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"
