"""A human-readable history of one invoice, built from the audit log.

The audit log already records every change to the invoice, its payment links, payments
and refunds, so the timeline is just a filtered, labelled view of it.
"""

from django.db.models import Q

from apps.audit.models import AuditLog

LABELS = {
    "invoice.created": "Draft created",
    "invoice.updated": "Draft edited",
    "invoice.issued": "Invoice issued",
    "invoice.cancelled": "Invoice cancelled",
    "payment_attempt.created": "Payment link created",
    "payment.captured": "Payment received",
    "refund.requested": "Refund requested",
    "refund.approved": "Refund approved",
    "refund.rejected": "Refund rejected",
    "refund.processed": "Refund completed",
}


def invoice_timeline(invoice) -> list[dict]:
    payment_ids = [str(pk) for pk in invoice.payments.values_list("pk", flat=True)]
    attempt_ids = [str(pk) for pk in invoice.payment_attempts.values_list("pk", flat=True)]
    refund_ids = [str(pk) for pk in invoice.refunds.values_list("pk", flat=True)]

    entries = (
        AuditLog.objects.filter(
            Q(entity_type="billing.invoice", entity_id=str(invoice.pk))
            | Q(entity_type="payments.payment", entity_id__in=payment_ids)
            | Q(entity_type="payments.paymentattempt", entity_id__in=attempt_ids)
            | Q(entity_type="payments.refund", entity_id__in=refund_ids),
            action__in=LABELS.keys(),
        )
        .select_related("actor")
        .order_by("at", "id")
    )

    timeline = []
    for entry in entries:
        after = entry.after or {}
        detail = ""
        if entry.action == "invoice.issued":
            detail = after.get("number") or ""
        elif entry.action == "invoice.cancelled":
            detail = after.get("cancel_reason") or ""
        elif entry.action == "payment.captured":
            detail = (after.get("method") or "").upper()
        elif entry.action in ("refund.requested", "refund.rejected"):
            detail = (
                after.get("reason")
                if entry.action == "refund.requested"
                else after.get("decision_note")
            )
        timeline.append(
            {
                "at": entry.at,
                "action": entry.action,
                "label": LABELS[entry.action],
                "detail": detail or "",
                "amount_paise": after.get("amount_paise")
                if entry.action.startswith(("payment", "refund"))
                else after.get("total_paise")
                if entry.action == "invoice.issued"
                else None,
                "actor": entry.actor.get_username() if entry.actor else "System",
            }
        )
    return timeline
