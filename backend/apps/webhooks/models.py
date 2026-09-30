from django.db import models


class WebhookEvent(models.Model):
    """Every verified webhook, stored raw before any processing.

    The unique `gateway_event_id` is what makes duplicate deliveries harmless: a second
    copy fails the insert at the database level, even if both copies arrive in the same
    millisecond. The raw payload makes any event replayable after a bug fix.
    """

    gateway_event_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=64, db_index=True)
    raw_payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [
            models.Index(
                fields=["received_at"],
                condition=models.Q(processed_at__isnull=True),
                name="webhook_unprocessed_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} {self.gateway_event_id}"
