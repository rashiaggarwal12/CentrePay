import django_filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CentreScopedMixin
from apps.payments import services as payment_services
from apps.payments.serializers import (
    CashSerializer,
    CollectSerializer,
    PaymentAttemptSerializer,
    PaymentSerializer,
)

from . import services
from .models import Invoice, InvoiceStatus, Service
from .serializers import (
    InvoiceActionSerializer,
    InvoiceCancelSerializer,
    InvoiceDetailSerializer,
    InvoiceListSerializer,
    InvoiceWriteSerializer,
    ServiceSerializer,
)


class ServiceViewSet(CentreScopedMixin, viewsets.ReadOnlyModelViewSet):
    """The centre's active catalogue. Managers edit services in Django Admin."""

    queryset = Service.objects.filter(is_active=True)
    serializer_class = ServiceSerializer
    pagination_class = None


class InvoiceFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=InvoiceStatus.choices)
    # Calendar date in the centre's timezone (Asia/Kolkata), not UTC.
    date = django_filters.DateFilter(field_name="created_at", lookup_expr="date")

    class Meta:
        model = Invoice
        fields = ["status", "date", "customer"]


class InvoiceViewSet(
    CentreScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Invoice.objects.select_related("customer", "created_by__user")
    filterset_class = InvoiceFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action != "list":
            qs = qs.prefetch_related(
                "items",
                "payments",
                "payment_attempts",
                "refunds__requested_by__user",
                "refunds__approved_by__user",
                "refunds__payment",
            )
        return qs

    def get_serializer_class(self):
        return InvoiceListSerializer if self.action == "list" else InvoiceDetailSerializer

    def _respond(self, invoice, status_code=status.HTTP_200_OK):
        invoice = self.get_queryset().get(pk=invoice.pk)
        return Response(
            InvoiceDetailSerializer(invoice, context=self.get_serializer_context()).data,
            status=status_code,
        )

    def create(self, request):
        serializer = InvoiceWriteSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        invoice = services.create_draft(
            staff=self.staff,
            customer=serializer.validated_data["customer"],
            lines=serializer.lines(),
            discount_paise=serializer.validated_data["discount_paise"],
        )
        return self._respond(invoice, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        invoice = self.get_object()  # 404 if not in this centre
        serializer = InvoiceWriteSerializer(
            data=request.data, partial=True, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = services.update_draft(
            invoice.pk,
            staff=self.staff,
            customer=data.get("customer"),
            lines=serializer.lines(),
            discount_paise=data.get("discount_paise"),
            expected_version=data.get("version"),
        )
        return self._respond(invoice)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        invoice = self.get_object()
        serializer = InvoiceActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = services.issue_invoice(
            invoice.pk, staff=self.staff, expected_version=serializer.validated_data.get("version")
        )
        return self._respond(invoice)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        invoice = self.get_object()
        serializer = InvoiceCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = services.cancel_invoice(
            invoice.pk,
            staff=self.staff,
            reason=serializer.validated_data["reason"],
            expected_version=serializer.validated_data.get("version"),
        )
        return self._respond(invoice)

    @action(detail=True, methods=["post"])
    def collect(self, request, pk=None):
        """Create (or return the existing) payment link for this invoice.

        Requires an `Idempotency-Key` header: the app generates one per "Collect" tap and
        reuses it on retries, so a flaky network never creates two links.
        """
        invoice = self.get_object()
        serializer = CollectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attempt, created = payment_services.collect(
            invoice.pk,
            staff=self.staff,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            amount_paise=serializer.validated_data.get("amount_paise"),
        )
        return Response(
            PaymentAttemptSerializer(attempt).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def cash(self, request, pk=None):
        """Record cash taken at the desk. Idempotency-Key header required."""
        invoice = self.get_object()
        serializer = CashSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = payment_services.record_cash_payment(
            invoice.pk,
            staff=self.staff,
            amount_paise=serializer.validated_data.get("amount_paise"),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
        )
        return Response(
            PaymentSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
