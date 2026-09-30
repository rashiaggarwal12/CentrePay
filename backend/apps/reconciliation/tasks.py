from datetime import date, timedelta

from celery import shared_task
from django.utils import timezone

from .engine import run_reconciliation


@shared_task(acks_late=True)
def reconcile_day(day: str | None = None) -> dict:
    """Scheduled at 02:00 IST for the previous day (see CELERY_BEAT_SCHEDULE)."""
    target = date.fromisoformat(day) if day else timezone.localdate() - timedelta(days=1)
    run = run_reconciliation(target)
    return {"run": run.pk, "status": run.status, "summary": run.summary}
