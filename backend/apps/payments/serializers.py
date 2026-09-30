from rest_framework import serializers

from .models import Payment, PaymentAttempt, Refund


class PaymentAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentAttempt
        fields = [
            "id",
            "amount_paise",
            "status",
            "short_url",
            "gateway_link_id",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    refundable_paise = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "gateway_payment_id",
            "amount_paise",
            "method",
            "status",
            "captured_at",
            "error_description",
            "refundable_paise",
            "created_at",
        ]
        read_only_fields = fields

    def get_refundable_paise(self, payment) -> int:
        """What a new refund request may still ask for (pending refunds already count),
        so the app never has to work it out itself."""
        if payment.status != Payment.Status.CAPTURED:
            return 0
        reserved = sum(
            r.amount_paise
            for r in payment.refunds.all()  # uses the prefetch on the invoice detail
            if r.status in Refund.RESERVING_STATUSES
        )
        return payment.amount_paise - reserved


class CollectSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(
        min_value=1, required=False, help_text="Defaults to the full amount due"
    )


class RefundSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(
        source="requested_by.user.get_username", read_only=True, default=None
    )
    approved_by = serializers.CharField(
        source="approved_by.user.get_username", read_only=True, default=None
    )
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    payment_method = serializers.CharField(source="payment.method", read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "payment",
            "payment_method",
            "amount_paise",
            "reason",
            "status",
            "requested_by",
            "approved_by",
            "decision_note",
            "decided_at",
            "processed_at",
            "last_error",
            "created_at",
        ]
        read_only_fields = fields


class RefundRequestSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=255)


class RefundRejectSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255)


class CashSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(
        min_value=1, required=False, help_text="Defaults to the full amount due"
    )
