from rest_framework import serializers

from apps.customers.models import Customer
from apps.payments.serializers import (
    PaymentAttemptSerializer,
    PaymentSerializer,
    RefundSerializer,
)

from .models import Invoice, InvoiceItem, Service
from .services import LineInput


class CentreScopedPKField(serializers.PrimaryKeyRelatedField):
    """A PK field whose choices are limited to the requesting staff member's centre,
    so another centre's customer/service ID is simply 'does not exist'."""

    def __init__(self, model, extra_filter=None, **kwargs):
        self.model = model
        self.extra_filter = extra_filter or {}
        super().__init__(**kwargs)

    def get_queryset(self):
        centre = self.context["request"].user.staff.centre
        return self.model.objects.filter(centre=centre, **self.extra_filter)


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "name", "price_paise", "gst_rate_bps", "is_active"]


# --- Read ---------------------------------------------------------------------------------------


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = [
            "id",
            "service",
            "description",
            "qty",
            "unit_price_paise",
            "gst_rate_bps",
            "line_total_paise",
            "discount_paise",
            "tax_paise",
        ]


class CustomerSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone"]


class InvoiceListSerializer(serializers.ModelSerializer):
    customer = CustomerSummarySerializer(read_only=True)
    amount_due_paise = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "status",
            "customer",
            "total_paise",
            "amount_paid_paise",
            "amount_refunded_paise",
            "amount_due_paise",
            "created_at",
            "issued_at",
            "version",
        ]
        read_only_fields = fields


class InvoiceDetailSerializer(InvoiceListSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)
    created_by = serializers.CharField(source="created_by.user.get_username", read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    payment_attempts = PaymentAttemptSerializer(many=True, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)

    class Meta(InvoiceListSerializer.Meta):
        fields = InvoiceListSerializer.Meta.fields + [
            "items",
            "payments",
            "payment_attempts",
            "refunds",
            "subtotal_paise",
            "discount_paise",
            "tax_paise",
            "created_by",
            "cancelled_at",
            "cancel_reason",
            "updated_at",
        ]
        read_only_fields = fields


# --- Write --------------------------------------------------------------------------------------


class InvoiceLineWriteSerializer(serializers.Serializer):
    service = CentreScopedPKField(Service, extra_filter={"is_active": True})
    qty = serializers.IntegerField(min_value=1, max_value=999)
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)

    @staticmethod
    def to_line(data) -> LineInput:
        return LineInput(
            service=data["service"], qty=data["qty"], description=data.get("description", "")
        )


class InvoiceWriteSerializer(serializers.Serializer):
    """Input for create (POST) and edit (PATCH). Prices come from the catalogue, never
    from the client; the server is the only thing that computes totals."""

    customer = CentreScopedPKField(Customer)
    items = InvoiceLineWriteSerializer(many=True, allow_empty=False)
    discount_paise = serializers.IntegerField(min_value=0, required=False, default=0)
    version = serializers.IntegerField(
        min_value=1, required=False, help_text="Current version, for edit conflict detection"
    )

    def lines(self) -> list[LineInput] | None:
        items = self.validated_data.get("items")
        if items is None:
            return None
        return [InvoiceLineWriteSerializer.to_line(item) for item in items]


class InvoicePreviewSerializer(serializers.Serializer):
    items = InvoiceLineWriteSerializer(many=True, allow_empty=True)
    discount_paise = serializers.IntegerField(min_value=0, required=False, default=0)

    def lines(self) -> list[LineInput]:
        return [InvoiceLineWriteSerializer.to_line(item) for item in self.validated_data["items"]]


class InvoiceActionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1, required=False)


class InvoiceCancelSerializer(InvoiceActionSerializer):
    reason = serializers.CharField(max_length=255)
