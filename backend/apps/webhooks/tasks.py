import logging

from celery import shared_task

from .services import process_event, record_failure

logger = logging.getLogger(__name__)

MAX_RETRIES = 8  # ~1s, 2s, 4s ... ~4 min: covers deploys and brief DB outages


@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True)
def process_webhook_event(self, event_pk: int, force: bool = False) -> str:
    try:
        return process_event(event_pk, force=force)
    except Exception as exc:
        record_failure(event_pk, exc)
        logger.exception(
            "webhook.processing_failed pk=%s attempt=%s", event_pk, self.request.retries + 1
        )
        # After the last retry the event stays unprocessed in the DB: the replay command
        # and the reconciliation job's stuck-event check pick it up from there.
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
