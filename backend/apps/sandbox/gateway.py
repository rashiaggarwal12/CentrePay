"""A stand-in for RazorpayClient with the same methods and Razorpay-shaped responses.

It keeps its state in the sandbox tables and sends the same webhooks Razorpay would
(through `delivery.deliver`). The rest of the app can't tell the difference, which is
the point: switching to real Razorpay is only a config change.
"""

import secrets
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.money import percent_of
from apps.payments.gateway import GatewayError

from .models import FakeLink, FakePayment, FakeRefund

FEE_BPS = 200  # 2% gateway fee
FEE_GST_BPS = 1800  # 18% GST on the fee


def fake_id(prefix: str) -> str:
    return f"{prefix}_S{secrets.token_hex(7)}"  # 'S' marks sandbox ids


def unix(dt) -> int:
    return int(dt.timestamp())


def fee_for(amount: int) -> tuple[int, int]:
    """(fee incl. GST, GST part), like Razorpay's `fee` and `tax` fields."""
    base = percent_of(amount, FEE_BPS)
    tax = percent_of(base, FEE_GST_BPS)
    return base + tax, tax


# --- Entity shapes ------------------------------------------------------------------------------


def link_entity(link: FakeLink, *, with_payments: bool = False) -> dict:
    data = {
        "id": link.id,
        "entity": "payment_link",
        "amount": link.amount,
        "amount_paid": sum(p.amount for p in link.payments.all() if p.status != "failed"),
        "currency": "INR",
        "status": link.status,
        "reference_id": link.reference_id,
        "description": link.description,
        "customer": link.customer,
        "notes": link.notes or [],
        "short_url": f"{settings.PUBLIC_BASE_URL}/sandbox/pay/{link.id}/",
        "expire_by": link.expire_by,
        "accept_partial": False,
        "created_at": unix(link.created_at),
    }
    if with_payments:
        data["payments"] = [
            {
                "payment_id": p.id,
                "amount": p.amount,
                "method": p.method,
                "status": p.status,
                "created_at": unix(p.created_at),
            }
            for p in link.payments.all()
        ]
    return data


def payment_entity(payment: FakePayment) -> dict:
    return {
        "id": payment.id,
        "entity": "payment",
        "amount": payment.amount,
        "currency": "INR",
        "status": payment.status,
        "order_id": payment.order_id,
        "method": payment.method,
        "amount_refunded": payment.amount_refunded,
        "refund_status": (
            None
            if not payment.amount_refunded
            else "full"
            if payment.amount_refunded >= payment.amount
            else "partial"
        ),
        "captured": payment.status in ("captured", "refunded"),
        "vpa": "success@sandbox" if payment.method == "upi" else None,
        "notes": payment.notes or [],
        "fee": payment.fee if payment.status != "failed" else None,
        "tax": payment.tax if payment.status != "failed" else None,
        "error_code": payment.error_code or None,
        "error_description": payment.error_description or None,
        "created_at": unix(payment.created_at),
    }


def refund_entity(refund: FakeRefund) -> dict:
    return {
        "id": refund.id,
        "entity": "refund",
        "amount": refund.amount,
        "currency": "INR",
        "payment_id": refund.payment_id,
        "receipt": refund.receipt or None,
        "notes": refund.notes or [],
        "status": refund.status,
        "speed_processed": "normal",
        "created_at": unix(refund.created_at),
    }


def _not_found(what: str, ident: str):
    return GatewayError(
        f"The id provided does not exist: {what} {ident}", status_code=400, code="BAD_REQUEST_ERROR"
    )


# --- The client -------------------------------------------------------------------------------


class FakeGateway:
    # Payment links

    def create_payment_link(
        self, *, amount_paise, reference_id, description, customer, expire_by, notes
    ) -> dict:
        if FakeLink.objects.filter(reference_id=reference_id).exists():
            raise GatewayError(
                "reference_id already exists", status_code=400, code="BAD_REQUEST_ERROR"
            )
        link = FakeLink.objects.create(
            id=fake_id("plink"),
            reference_id=reference_id,
            amount=amount_paise,
            description=description,
            customer=customer,
            notes=notes,
            expire_by=expire_by,
        )
        return link_entity(link)

    def find_payment_link_by_reference(self, reference_id: str) -> dict | None:
        link = FakeLink.objects.filter(reference_id=reference_id).first()
        return link_entity(link) if link else None

    def fetch_payment_link(self, link_id: str) -> dict:
        link = FakeLink.objects.filter(pk=link_id).first()
        if link is None:
            raise _not_found("payment_link", link_id)
        return link_entity(link, with_payments=True)

    def cancel_payment_link(self, link_id: str) -> dict:
        link = FakeLink.objects.filter(pk=link_id).first()
        if link is None:
            raise _not_found("payment_link", link_id)
        if link.status != "created":
            raise GatewayError(
                f"Payment link cannot be cancelled in {link.status} state",
                status_code=400,
                code="BAD_REQUEST_ERROR",
            )
        link.status = "cancelled"
        link.save(update_fields=["status"])
        return link_entity(link)

    # Payments

    def fetch_payment(self, payment_id: str) -> dict:
        payment = FakePayment.objects.filter(pk=payment_id).first()
        if payment is None:
            raise _not_found("payment", payment_id)
        return payment_entity(payment)

    def iter_payments(self, from_ts: int, to_ts: int, page_size: int = 100):
        qs = FakePayment.objects.filter(
            created_at__gte=datetime.fromtimestamp(from_ts, tz=UTC),
            created_at__lte=datetime.fromtimestamp(to_ts, tz=UTC),
        ).order_by("created_at")
        for payment in qs:
            yield payment_entity(payment)

    # Refunds

    def create_refund(self, payment_id: str, *, amount_paise: int, receipt: str, notes: dict):
        with transaction.atomic():
            payment = FakePayment.objects.select_for_update().filter(pk=payment_id).first()
            if payment is None:
                raise _not_found("payment", payment_id)
            if payment.status not in ("captured", "refunded"):
                raise GatewayError(
                    "Only captured payments can be refunded",
                    status_code=400,
                    code="BAD_REQUEST_ERROR",
                )
            if amount_paise > payment.amount - payment.amount_refunded:
                raise GatewayError(
                    "The refund amount provided is greater than amount captured",
                    status_code=400,
                    code="BAD_REQUEST_ERROR",
                )
            refund = FakeRefund.objects.create(
                id=fake_id("rfnd"),
                payment=payment,
                amount=amount_paise,
                receipt=receipt,
                notes=notes,
                created_at=timezone.now(),
            )
            payment.amount_refunded += amount_paise
            if payment.amount_refunded >= payment.amount:
                payment.status = "refunded"
            payment.save(update_fields=["amount_refunded", "status"])
            entity = refund_entity(refund)
            payment_data = payment_entity(payment)

        # Like Razorpay, the refund.processed webhook arrives separately from the API response.
        from .delivery import deliver

        transaction.on_commit(
            lambda: deliver("refund.processed", refund=entity, payment=payment_data)
        )
        return entity

    def list_refunds(self, payment_id: str) -> list[dict]:
        return [refund_entity(r) for r in FakeRefund.objects.filter(payment_id=payment_id)]

    # Settlements: everything captured/refunded on day D settles on day D+1.

    def settlement_recon(self, year: int, month: int, day: int) -> list[dict]:
        settled_on = datetime(year, month, day).date()
        business_day = settled_on - timedelta(days=1)
        settlement_id = f"setl_S{business_day:%Y%m%d}"
        items = []
        for p in FakePayment.objects.exclude(status="failed"):
            if timezone.localdate(p.created_at) == business_day:
                items.append(
                    {
                        "entity_id": p.id,
                        "type": "payment",
                        "amount": p.amount,
                        "fee": p.fee,
                        "tax": p.tax,
                        "credit": p.amount - p.fee,
                        "debit": 0,
                        "settlement_id": settlement_id,
                        "payment_id": p.id,
                        "settled": True,
                    }
                )
        for r in FakeRefund.objects.select_related("payment"):
            if timezone.localdate(r.created_at) == business_day:
                items.append(
                    {
                        "entity_id": r.id,
                        "type": "refund",
                        "amount": r.amount,
                        "fee": 0,
                        "tax": 0,
                        "credit": 0,
                        "debit": r.amount,
                        "settlement_id": settlement_id,
                        "payment_id": r.payment_id,
                        "settled": True,
                    }
                )
        return items

    def fetch_settlement(self, settlement_id: str) -> dict:
        business_day = datetime.strptime(settlement_id.removeprefix("setl_S"), "%Y%m%d").date()
        settled_on = business_day + timedelta(days=1)
        items = self.settlement_recon(settled_on.year, settled_on.month, settled_on.day)
        return {
            "id": settlement_id,
            "entity": "settlement",
            "amount": sum(i["credit"] for i in items) - sum(i["debit"] for i in items),
            "status": "processed",
            "fees": sum(i["fee"] for i in items),
            "tax": sum(i["tax"] for i in items),
            "utr": f"SANDBOXUTR{business_day:%Y%m%d}",
        }


# --- Customer actions on the sandbox payment page -----------------------------------------------


def pay_link(
    link: FakeLink,
    *,
    method: str = "upi",
    amount: int | None = None,
    deliver_times: int = 1,
    send_webhooks: bool = True,
) -> FakePayment:
    """Simulate the customer paying a link. `deliver_times` > 1 repeats each webhook with
    the same event id (gateway retries); `send_webhooks=False` simulates lost webhooks."""
    from .delivery import deliver

    amount = link.amount if amount is None else amount
    fee, tax = fee_for(amount)
    with transaction.atomic():
        payment = FakePayment.objects.create(
            id=fake_id("pay"),
            link=link,
            order_id=fake_id("order"),
            amount=amount,
            method=method,
            status="captured",
            fee=fee,
            tax=tax,
            notes=link.notes,
            created_at=timezone.now(),
        )
        link.status = "paid"
        link.save(update_fields=["status"])

    if send_webhooks:
        payment_data = payment_entity(payment)
        deliver("payment.captured", times=deliver_times, payment=payment_data)
        deliver(
            "payment_link.paid",
            times=deliver_times,
            payment_link=link_entity(link),
            payment=payment_data,
        )
    return payment


def fail_link_payment(link: FakeLink, *, method: str = "upi") -> FakePayment:
    from .delivery import deliver

    payment = FakePayment.objects.create(
        id=fake_id("pay"),
        link=link,
        order_id=fake_id("order"),
        amount=link.amount,
        method=method,
        status="failed",
        notes=link.notes,
        error_code="BAD_REQUEST_ERROR",
        error_description="Payment was declined by the customer's bank (sandbox)",
        created_at=timezone.now(),
    )
    deliver("payment.failed", payment=payment_entity(payment))
    return payment


def expire_link(link: FakeLink) -> None:
    from .delivery import deliver

    if link.status != "created":
        return
    link.status = "expired"
    link.save(update_fields=["status"])
    deliver("payment_link.expired", payment_link=link_entity(link))
