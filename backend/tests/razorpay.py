"""Builders for Razorpay webhook payloads and API responses.

Shapes follow Razorpay's documented webhook payloads (payment.*, payment_link.*).
TODO: once test-mode keys are set up, replace these with recorded real payloads in
tests/fixtures/razorpay/ and keep these builders for variations.
"""

import hashlib
import hmac
import itertools
import json
import time

_ids = itertools.count(1)


def next_id(prefix: str) -> str:
    return f"{prefix}_T{next(_ids):012d}"


def payment_entity(
    *,
    payment_id=None,
    amount=100_000,
    status="captured",
    method="upi",
    notes=None,
    order_id=None,
    fee=None,
    error_code=None,
    error_description=None,
):
    return {
        "id": payment_id or next_id("pay"),
        "entity": "payment",
        "amount": amount,
        "currency": "INR",
        "status": status,
        "order_id": order_id or next_id("order"),
        "invoice_id": None,
        "international": False,
        "method": method,
        "amount_refunded": 0,
        "refund_status": None,
        "captured": status == "captured",
        "description": "Invoice payment",
        "vpa": "success@razorpay" if method == "upi" else None,
        "email": "void@razorpay.com",
        "contact": "+919812345678",
        "notes": notes if notes is not None else [],
        "fee": fee if fee is not None else (amount * 2 // 100 if status == "captured" else None),
        "tax": None,
        "error_code": error_code,
        "error_description": error_description,
        "created_at": int(time.time()),
    }


def link_entity(*, link_id, reference_id, amount, status="paid", amount_paid=None, notes=None):
    return {
        "id": link_id,
        "entity": "payment_link",
        "amount": amount,
        "amount_paid": amount if amount_paid is None and status == "paid" else (amount_paid or 0),
        "currency": "INR",
        "status": status,
        "reference_id": reference_id,
        "short_url": f"https://rzp.io/i/{link_id[-6:]}",
        "accept_partial": False,
        "notes": notes or [],
        "order_id": next_id("order"),
        "created_at": int(time.time()) - 60,
    }


def event(event_type: str, **entities) -> dict:
    return {
        "entity": "event",
        "account_id": "acc_TESTACCOUNT01",
        "event": event_type,
        "contains": list(entities),
        "payload": {name: {"entity": entity} for name, entity in entities.items()},
        "created_at": int(time.time()),
    }


def notes_for(attempt) -> dict:
    return {
        "invoice_id": str(attempt.invoice_id),
        "invoice_number": attempt.invoice.number,
        "attempt_id": str(attempt.pk),
    }


def captured_event(attempt, *, amount=None, payment_id=None, method="upi"):
    return event(
        "payment.captured",
        payment=payment_entity(
            payment_id=payment_id,
            amount=attempt.amount_paise if amount is None else amount,
            method=method,
            notes=notes_for(attempt),
        ),
    )


def link_paid_event(attempt, *, amount=None, payment_id=None):
    amount = attempt.amount_paise if amount is None else amount
    return event(
        "payment_link.paid",
        payment_link=link_entity(
            link_id=attempt.gateway_link_id,
            reference_id=attempt.reference_id,
            amount=attempt.amount_paise,
            amount_paid=amount,
            notes=notes_for(attempt),
        ),
        payment=payment_entity(payment_id=payment_id, amount=amount, notes=notes_for(attempt)),
    )


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def link_response(*, reference_id, amount, link_id=None, expire_by=None):
    link_id = link_id or next_id("plink")
    return {
        "id": link_id,
        "entity": "payment_link",
        "amount": amount,
        "amount_paid": 0,
        "currency": "INR",
        "status": "created",
        "reference_id": reference_id,
        "short_url": f"https://rzp.io/i/{link_id[-6:]}",
        "expire_by": expire_by or int(time.time()) + 20 * 60,
        "accept_partial": False,
        "notes": {},
    }
