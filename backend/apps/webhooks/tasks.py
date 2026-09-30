import logging

from celery import shared_task

from apps.common.exceptions import DependencyNotReady

from .services import give_up, process_event, record_failure

logger = logging.getLogger(__name__)

MAX_RETRIES = 8  # 1s, 2s, 4s ... ~4 min: covers deploys and brief DB outages
# Waiting for another event (e.g. a refund whose payment event hasn't landed) gets a
# longer runway: 30s, 60s, ... capped at 10 min, about 45 minutes in total.
DEPENDENCY_BACKOFF_CAP = 600


@shared_task(bind=True, max_retries=MAX_RETRIES, acks_late=True)
def process_webhook_event(self, event_pk: int, force: bool = False) -> str:
    try:
        return process_event(event_pk, force=force)
    except DependencyNotReady as exc:
        # Edge case 4: e.g. refund.processed before payment.captured. Wait and retry;
        # if it never resolves, raise an issue rather than retrying forever.
        record_failure(event_pk, exc)
        if self.request.retries >= self.max_retries:
            give_up(event_pk, exc)
            return "gave_up"
        countdown = min(30 * 2**self.request.retries, DEPENDENCY_BACKOFF_CAP)
        logger.info("webhook.waiting_for_dependency pk=%s retry_in=%ss", event_pk, countdown)
        raise self.retry(exc=exc, countdown=countdown) from exc
    except Exception as exc:
        record_failure(event_pk, exc)
        logger.exception(
            "webhook.processing_failed pk=%s attempt=%s", event_pk, self.request.retries + 1
        )
        # After the last retry the event stays unprocessed in the DB: the replay command
        # and the reconciliation job's stuck-event check pick it up from there.
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
