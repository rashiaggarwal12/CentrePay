from django.contrib import admin

from .models import Payment, PaymentAttempt


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money records change only through the service layer."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "invoice", "amount_paise", "status", "gateway_link_id"]
    list_filter = ["status"]
    search_fields = ["gateway_link_id", "reference_id", "invoice__number"]


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = [
        "created_at",
        "gateway_payment_id",
        "invoice",
        "amount_paise",
        "method",
        "status",
    ]
    list_filter = ["status", "method"]
    search_fields = ["gateway_payment_id", "invoice__number"]
    date_hierarchy = "created_at"
