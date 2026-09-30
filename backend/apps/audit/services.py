from django.forms.models import model_to_dict

from .models import AuditLog


def snapshot(instance, fields: list[str] | None = None) -> dict:
    """A JSON-friendly dict of a model instance's concrete fields."""
    return model_to_dict(instance, fields=fields)


def audit(
    action: str, instance, *, actor=None, before: dict | None = None, after: dict | None = None
) -> AuditLog:
    """Record an audit entry. Call inside the same transaction as the change it describes,
    so the log and the change commit (or roll back) together."""
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=instance._meta.label_lower,
        entity_id=str(instance.pk),
        before=before,
        after=after if after is not None else snapshot(instance),
    )
