import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .handlers import HANDLERS
from .models import WebhookEvent

logger = logging.getLogger(__name__)


def process_event(event_pk: int, *, force: bool = False) -> str:
    """Run the handler for a stored event, once.

    The event row is locked for the duration, so two workers can't process the same
    event concurrently. The handler's changes and `processed_at` commit together:
    if the handler fails, neither is saved and the event stays unprocessed.
    """
    with transaction.atomic():
        event = WebhookEvent.objects.select_for_update().get(pk=event_pk)
        if event.processed_at and not force:
            return "already_processed"

        handler = HANDLERS.get(event.event_type)
        if handler is None:
            outcome = "ignored"  # an event type we don't subscribe to; keep it for the record
        else:
            handler(event.raw_payload)
            outcome = "processed"

        event.processed_at = timezone.now()
        event.attempts += 1
        event.last_error = ""
        event.save(update_fields=["processed_at", "attempts", "last_error"])

    logger.info("webhook.%s event_id=%s type=%s", outcome, event.gateway_event_id, event.event_type)
    return outcome


def record_failure(event_pk: int, exc: Exception) -> None:
    """Called after the processing transaction rolled back, so it's a separate write."""
    WebhookEvent.objects.filter(pk=event_pk).update(
        attempts=F("attempts") + 1, last_error=f"{type(exc).__name__}: {exc}"[:2000]
    )
