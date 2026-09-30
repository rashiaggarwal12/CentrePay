"""Send signed webhooks from the fake gateway to our own webhook endpoint.

The request goes through the real `razorpay_webhook` view (signature check, dedup,
storage, async processing): only the network hop is skipped, so a single dev server
can't deadlock calling itself.
"""

import hashlib
import hmac
import json
import secrets
import time

from django.test import RequestFactory

from apps.payments.gateway import webhook_secret


def build_event(event_type: str, **entities) -> dict:
    return {
        "entity": "event",
        "account_id": "acc_SANDBOX0000001",
        "event": event_type,
        "contains": list(entities),
        "payload": {name: {"entity": entity} for name, entity in entities.items()},
        "created_at": int(time.time()),
    }


def deliver(event_type: str, *, times: int = 1, **entities) -> list[int]:
    """Deliver one event (optionally several times with the same event id, like a
    gateway retrying). Returns the HTTP status of each delivery."""
    from apps.webhooks.views import razorpay_webhook

    body = json.dumps(build_event(event_type, **entities), separators=(",", ":")).encode()
    signature = hmac.new(webhook_secret().encode(), body, hashlib.sha256).hexdigest()
    event_id = f"evt_{secrets.token_hex(7)}"
    statuses = []
    for _ in range(times):
        request = RequestFactory().post(
            "/webhooks/razorpay/",
            data=body,
            content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature,
            HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )
        statuses.append(razorpay_webhook(request).status_code)
    return statuses
