"""Daily reconciliation: compare what the gateway says happened with what we recorded.

    python manage.py reconcile --date 2026-10-01
    (and Celery Beat runs it for "yesterday" at 02:00 IST)

The run is safe to repeat: recoveries go through the same idempotent service calls as
webhooks, and issues are deduplicated per (kind, gateway_ref) while open.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from apps.billing.models import Invoice
from apps.ledger.models import LedgerEntry
from apps.payments import services as payments
from apps.payments.gateway import GatewayError, get_gateway
from apps.payments.models import Payment, PaymentAttempt, Refund
from apps.payments.refunds import CASH_PREFIX
from apps.webhooks.models import WebhookEvent

from .models import ReconciliationRun
from .services import Kind, raise_issue

logger = logging.getLogger(__name__)

STUCK_AFTER = timedelta(hours=1)
CAPTURED_AT_GATEWAY = {"captured", "refunded"}


@dataclass
class Context:
    run: ReconciliationRun
    day: date
    gateway: object | None
    start: datetime
    end: datetime
    issues: Counter = field(default_factory=Counter)
    stats: Counter = field(default_factory=Counter)

    def issue(self, kind, gateway_ref, **kwargs):
        raise_issue(kind, gateway_ref, run=self.run, **kwargs)
        self.issues[kind] += 1


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """[00:00, 24:00) of an IST calendar day, as aware datetimes."""
    tz = timezone.get_current_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def run_reconciliation(day: date) -> ReconciliationRun:
    start, end = day_bounds(day)
    run = ReconciliationRun.objects.create(date=day)
    try:
        gateway = get_gateway()
    except GatewayError as exc:
        gateway = None
        gateway_error = str(exc)
    else:
        gateway_error = ""
    ctx = Context(run=run, day=day, gateway=gateway, start=start, end=end)

    failure = None
    gateway_checks = [
        sweep_open_links,
        check_gateway_payments,
        check_local_payments,
        check_settlements,
    ]
    for check in gateway_checks if gateway else []:
        try:
            check(ctx)
        except GatewayError as exc:
            # Keep going: local checks are still worth running while the gateway is down.
            failure = f"{check.__name__}: {exc}"
            logger.warning("reconciliation.gateway_error check=%s error=%s", check.__name__, exc)
            break
    check_invoice_drift(ctx)
    check_stuck_events(ctx)

    run.finished_at = timezone.now()
    run.status = (
        ReconciliationRun.Status.FAILED
        if failure or gateway_error
        else ReconciliationRun.Status.SUCCEEDED
    )
    run.summary = {
        "issues": dict(ctx.issues),
        "issues_total": sum(ctx.issues.values()),
        **dict(ctx.stats),
        **({"error": failure or gateway_error} if (failure or gateway_error) else {}),
    }
    run.save()
    logger.info(
        "reconciliation.finished date=%s status=%s summary=%s", day, run.status, run.summary
    )
    return run


# --- Checks -------------------------------------------------------------------------------------


def sweep_open_links(ctx: Context) -> None:
    """Links still 'created' or 'expired' locally may have been paid with every webhook
    lost. Ask the gateway, and apply any captured payment we're missing (edge case 7)."""
    attempts = PaymentAttempt.objects.filter(
        status__in=[PaymentAttempt.Status.CREATED, PaymentAttempt.Status.EXPIRED],
        gateway_link_id__isnull=False,
        created_at__lt=ctx.end,
        created_at__gte=ctx.start - timedelta(days=1),
    )
    for attempt in attempts:
        link = ctx.gateway.fetch_payment_link(attempt.gateway_link_id)
        ctx.stats["links_checked"] += 1
        for p in link.get("payments") or []:
            if p.get("status") != "captured":
                continue
            if Payment.objects.filter(
                gateway_payment_id=p["payment_id"], status=Payment.Status.CAPTURED
            ).exists():
                continue
            full = ctx.gateway.fetch_payment(p["payment_id"])
            _recover(ctx, full, invoice_id=attempt.invoice_id, attempt=attempt)


def check_gateway_payments(ctx: Context) -> None:
    for gp in ctx.gateway.iter_payments(int(ctx.start.timestamp()), int(ctx.end.timestamp()) - 1):
        ctx.stats["gateway_payments"] += 1
        if gp.get("status") not in CAPTURED_AT_GATEWAY:
            continue
        local = Payment.objects.filter(gateway_payment_id=gp["id"]).first()

        if local is None or local.status != Payment.Status.CAPTURED:
            invoice_id, attempt = payments.resolve_payment_target(gp)
            if invoice_id is None:
                ctx.issue(
                    Kind.MISSING_LOCALLY,
                    gp["id"],
                    actual_paise=gp["amount"],
                    details={"method": gp.get("method"), "notes": gp.get("notes")},
                )
                continue
            _recover(ctx, gp, invoice_id=invoice_id, attempt=attempt)
            continue

        if local.amount_paise != gp["amount"]:
            ctx.issue(
                Kind.AMOUNT_MISMATCH,
                gp["id"],
                invoice=local.invoice,
                expected_paise=local.amount_paise,
                actual_paise=gp["amount"],
                details={"source": "reconciliation"},
            )

        # Refunds: the gateway's refunded total vs refunds we've finalised.
        if local.refunds.filter(status=Refund.Status.PROCESSING).exists():
            continue  # in flight; tomorrow's run will compare
        local_refunded = (
            local.refunds.filter(status=Refund.Status.PROCESSED).aggregate(t=Sum("amount_paise"))[
                "t"
            ]
            or 0
        )
        if local_refunded != int(gp.get("amount_refunded") or 0):
            ctx.issue(
                Kind.STATUS_MISMATCH,
                gp["id"],
                invoice=local.invoice,
                expected_paise=local_refunded,
                actual_paise=int(gp.get("amount_refunded") or 0),
                details={"field": "amount_refunded", "gateway_status": gp.get("status")},
            )


def check_local_payments(ctx: Context) -> None:
    """Captured locally but unknown (or not captured) at the gateway. Should never
    happen; if it does, someone needs to look before money is assumed to exist."""
    local = Payment.objects.filter(
        status=Payment.Status.CAPTURED, captured_at__gte=ctx.start, captured_at__lt=ctx.end
    ).exclude(gateway_payment_id__startswith=CASH_PREFIX)
    for payment in local.select_related("invoice"):
        ctx.stats["local_payments"] += 1
        try:
            gp = ctx.gateway.fetch_payment(payment.gateway_payment_id)
        except GatewayError as exc:
            if exc.retryable:
                raise
            ctx.issue(
                Kind.MISSING_AT_GATEWAY,
                payment.gateway_payment_id,
                invoice=payment.invoice,
                expected_paise=payment.amount_paise,
                details={"error": str(exc)},
            )
            continue
        if gp.get("status") not in CAPTURED_AT_GATEWAY:
            ctx.issue(
                Kind.STATUS_MISMATCH,
                payment.gateway_payment_id,
                invoice=payment.invoice,
                details={"local_status": payment.status, "gateway_status": gp.get("status")},
            )


def check_invoice_drift(ctx: Context) -> None:
    """Invariants, for every invoice that has seen money:
    amount_paid == Σ captured payments, amount_refunded == Σ processed refunds,
    and ledger credits − debits == amount_paid − amount_refunded."""
    captured = dict(
        Payment.objects.filter(status=Payment.Status.CAPTURED)
        .values("invoice_id")
        .annotate(t=Sum("amount_paise"))
        .values_list("invoice_id", "t")
    )
    refunded = dict(
        Refund.objects.filter(status=Refund.Status.PROCESSED)
        .values("invoice_id")
        .annotate(t=Sum("amount_paise"))
        .values_list("invoice_id", "t")
    )
    ledger = {
        row["invoice_id"]: (row["c"] or 0) - (row["d"] or 0)
        for row in LedgerEntry.objects.values("invoice_id").annotate(
            c=Sum("credit_paise"), d=Sum("debit_paise")
        )
    }
    invoices = Invoice.objects.filter(
        Q(amount_paid_paise__gt=0) | Q(pk__in=captured.keys()) | Q(pk__in=ledger.keys())
    )
    for invoice in invoices:
        ctx.stats["invoices_checked"] += 1
        exp_paid = captured.get(invoice.pk, 0)
        exp_refunded = refunded.get(invoice.pk, 0)
        net = invoice.amount_paid_paise - invoice.amount_refunded_paise
        problems = {}
        if invoice.amount_paid_paise != exp_paid:
            problems["amount_paid"] = [invoice.amount_paid_paise, exp_paid]
        if invoice.amount_refunded_paise != exp_refunded:
            problems["amount_refunded"] = [invoice.amount_refunded_paise, exp_refunded]
        if ledger.get(invoice.pk, 0) != net:
            problems["ledger_net"] = [ledger.get(invoice.pk, 0), net]
        if problems:
            ctx.issue(
                Kind.INVOICE_DRIFT,
                f"invoice:{invoice.pk}",
                invoice=invoice,
                local_ref=invoice.number or "",
                expected_paise=exp_paid,
                actual_paise=invoice.amount_paid_paise,
                details={"mismatches": problems},
            )


def check_settlements(ctx: Context) -> None:
    """For money settled today: each settlement's amount should equal what our own
    records say it should be: Σ(payment − fee) − Σ(refunds)."""
    items = ctx.gateway.settlement_recon(ctx.day.year, ctx.day.month, ctx.day.day)
    by_settlement: dict[str, list[dict]] = {}
    for item in items:
        by_settlement.setdefault(item["settlement_id"], []).append(item)

    for settlement_id, rows in by_settlement.items():
        ctx.stats["settlements_checked"] += 1
        expected, unknown = 0, []
        for item in rows:
            if item["type"] == "payment":
                local = Payment.objects.filter(gateway_payment_id=item["entity_id"]).first()
                if local is None:
                    unknown.append(item["entity_id"])
                    expected += item.get("credit", 0)
                    continue
                expected += local.amount_paise - local.fee_paise
                if local.fee_paise and local.fee_paise != item.get("fee"):
                    ctx.issue(
                        Kind.SETTLEMENT_MISMATCH,
                        item["entity_id"],
                        invoice=local.invoice,
                        expected_paise=local.fee_paise,
                        actual_paise=item.get("fee"),
                        details={"field": "fee", "settlement_id": settlement_id},
                    )
            elif item["type"] == "refund":
                local = Refund.objects.filter(gateway_refund_id=item["entity_id"]).first()
                if local is None:
                    unknown.append(item["entity_id"])
                    expected -= item.get("debit", 0)
                    continue
                expected -= local.amount_paise
            else:
                # adjustments, disputes...: take the gateway's figure, but show it.
                unknown.append(item["entity_id"])
                expected += item.get("credit", 0) - item.get("debit", 0)

        settlement = ctx.gateway.fetch_settlement(settlement_id)
        if settlement["amount"] != expected or unknown:
            ctx.issue(
                Kind.SETTLEMENT_MISMATCH,
                settlement_id,
                expected_paise=expected,
                actual_paise=settlement["amount"],
                details={"unknown_entities": unknown, "utr": settlement.get("utr")},
            )


def check_stuck_events(ctx: Context) -> None:
    cutoff = timezone.now() - STUCK_AFTER
    for event in WebhookEvent.objects.filter(processed_at__isnull=True, received_at__lt=cutoff):
        ctx.issue(
            Kind.STUCK_EVENT,
            event.gateway_event_id,
            local_ref=str(event.pk),
            details={
                "event_type": event.event_type,
                "attempts": event.attempts,
                "last_error": event.last_error[:500],
            },
        )


# --- Recovery -----------------------------------------------------------------------------------


def _recover(ctx: Context, gp: dict, *, invoice_id: int, attempt) -> None:
    _, applied = payments.apply_payment_captured(
        gateway_payment_id=gp["id"],
        invoice_id=invoice_id,
        amount_paise=int(gp["amount"]),
        method=gp.get("method") or "",
        attempt=attempt,
        order_id=gp.get("order_id") or "",
        fee_paise=int(gp.get("fee") or 0),
    )
    if applied:
        ctx.stats["recovered_payments"] += 1
        logger.warning(
            "reconciliation.recovered_payment payment=%s invoice=%s amount=%s",
            gp["id"],
            invoice_id,
            gp["amount"],
        )
