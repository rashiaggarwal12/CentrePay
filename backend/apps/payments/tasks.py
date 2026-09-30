from celery import shared_task

from .gateway import GatewayError
from .services import cancel_open_links


@shared_task(
    autoretry_for=(GatewayError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
    acks_late=True,
)
def cancel_superseded_links(invoice_id: int) -> int:
    """After a payment, cancel live links for more than is now due (all of them once paid)."""
    return cancel_open_links(invoice_id, only_exceeding_due=True)


@shared_task(
    autoretry_for=(GatewayError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
    acks_late=True,
)
def cancel_all_links(invoice_id: int) -> int:
    """After an invoice is cancelled, kill every live link so it can't be paid."""
    return cancel_open_links(invoice_id, only_exceeding_due=False)


@shared_task(bind=True, max_retries=8, acks_late=True)
def process_refund(self, refund_id: int) -> str:
    from .refunds import submit_to_gateway

    try:
        return submit_to_gateway(refund_id)
    except GatewayError as exc:  # only retryable ones reach here (4xx mark the refund failed)
        raise self.retry(exc=exc, countdown=min(2**self.request.retries * 5, 600)) from exc
