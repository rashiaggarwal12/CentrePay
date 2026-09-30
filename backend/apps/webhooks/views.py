import hashlib
import hmac
import json
import logging

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.gateway import webhook_secret

from .models import WebhookEvent
from .tasks import process_webhook_event

logger = logging.getLogger(__name__)


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)  # constant-time compare


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Verify -> store raw -> enqueue -> 200. Nothing slow happens here, because Razorpay
    retries (and eventually disables the webhook) if we don't answer quickly."""
    body = request.body  # raw bytes: the signature is over these exact bytes
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_signature(body, signature, webhook_secret()):
        logger.warning("webhook.invalid_signature ip=%s", request.META.get("REMOTE_ADDR"))
        return HttpResponse(status=400)

    try:
        payload = json.loads(body)
        event_type = payload["event"]
    except (ValueError, KeyError, TypeError):
        logger.warning("webhook.malformed_payload")
        return HttpResponse(status=400)

    # Razorpay sends a unique id per event; retries of the same event reuse it. If it's
    # ever missing, a hash of the signed body still dedupes identical redeliveries.
    event_id = request.headers.get("X-Razorpay-Event-Id") or (
        "sha256:" + hashlib.sha256(body).hexdigest()[:56]
    )

    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                gateway_event_id=event_id, event_type=event_type, raw_payload=payload
            )
    except IntegrityError:
        logger.info("webhook.duplicate event_id=%s type=%s", event_id, event_type)
        return HttpResponse(status=200)  # already stored: nothing to do

    logger.info("webhook.received event_id=%s type=%s pk=%s", event_id, event_type, event.pk)
    transaction.on_commit(lambda: process_webhook_event.delay(event.pk))
    return HttpResponse(status=200)
