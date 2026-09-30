from rest_framework import serializers

from .models import Payment, PaymentAttempt


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
            "created_at",
        ]
        read_only_fields = fields


class CollectSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(
        min_value=1, required=False, help_text="Defaults to the full amount due"
    )
