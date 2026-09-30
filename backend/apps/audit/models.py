from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AuditLog(models.Model):
    """Who changed what, and when. Rows are never updated or deleted."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )  # null = system (webhook, Celery task, reconciliation)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}#{self.entity_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("AuditLog is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("AuditLog is append-only")
