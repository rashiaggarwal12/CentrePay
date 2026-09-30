from datetime import date as date_cls

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice
from apps.payments.models import Payment, Refund


class DailyCollectionView(APIView):
    """Day-close totals for the caller's centre. Dates are IST calendar days.

    GET /reports/daily-collection/?date=2026-10-01   (defaults to today)
    """

    def get(self, request):
        raw = request.query_params.get("date")
        try:
            day = date_cls.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            raise ValidationError({"date": ["Use YYYY-MM-DD."]}) from None
        centre = request.user.staff.centre

        payments = Payment.objects.filter(
            invoice__centre=centre, status=Payment.Status.CAPTURED, captured_at__date=day
        )
        by_method = {
            row["method"] or "unknown": {
                "count": row["count"],
                "amount_paise": row["amount"],
                "gateway_fees_paise": row["fees"] or 0,
            }
            for row in payments.values("method")
            .annotate(count=Count("id"), amount=Sum("amount_paise"), fees=Sum("fee_paise"))
            .order_by("method")
        }
        collected = sum(m["amount_paise"] for m in by_method.values())
        fees = sum(m["gateway_fees_paise"] for m in by_method.values())

        refunds = Refund.objects.filter(
            invoice__centre=centre, status=Refund.Status.PROCESSED, processed_at__date=day
        ).aggregate(count=Count("id"), amount=Sum("amount_paise"))
        refunded = refunds["amount"] or 0

        invoices = Invoice.objects.filter(centre=centre)
        return Response(
            {
                "date": day.isoformat(),
                "centre": {"id": centre.id, "name": centre.name, "code": centre.code},
                "collections": {
                    "by_method": by_method,
                    "count": sum(m["count"] for m in by_method.values()),
                    "total_paise": collected,
                },
                "refunds": {"count": refunds["count"], "total_paise": refunded},
                "net_collection_paise": collected - refunded,
                "gateway_fees_paise": fees,
                "invoices": {
                    "created": invoices.filter(created_at__date=day).count(),
                    "issued": invoices.filter(issued_at__date=day).count(),
                    "cancelled": invoices.filter(cancelled_at__date=day).count(),
                },
                "pending_refund_approvals": Refund.objects.filter(
                    invoice__centre=centre, status=Refund.Status.REQUESTED
                ).count(),
            }
        )
