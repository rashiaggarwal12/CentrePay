import re

from rest_framework import serializers

from .models import Customer

_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def normalize_phone(value: str) -> str:
    """Accept '98123 45678', '+91-9812345678', '09812345678' -> '+919812345678'."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not _INDIAN_MOBILE.match(digits):
        raise serializers.ValidationError("Enter a valid 10-digit Indian mobile number.")
    return f"+91{digits}"


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_phone(self, value):
        phone = normalize_phone(value)
        centre = self.context["request"].user.staff.centre
        clash = Customer.objects.filter(centre=centre, phone=phone)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A customer with this phone already exists.")
        return phone
