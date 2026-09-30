from django.contrib import admin

from .models import Invoice, InvoiceItem, Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ["name", "centre", "price_paise", "gst_rate_bps", "is_active"]
    list_filter = ["centre", "is_active"]
    search_fields = ["name"]


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    can_delete = False
    readonly_fields = [
        "service",
        "description",
        "qty",
        "unit_price_paise",
        "gst_rate_bps",
        "line_total_paise",
        "discount_paise",
        "tax_paise",
    ]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Read-only: invoice changes must go through the service layer (state machine,
    locking, audit), so admin never edits invoices directly."""

    list_display = [
        "__str__",
        "centre",
        "customer",
        "status",
        "total_paise",
        "amount_paid_paise",
        "created_at",
    ]
    list_filter = ["centre", "status"]
    search_fields = ["number", "customer__name", "customer__phone"]
    date_hierarchy = "created_at"
    inlines = [InvoiceItemInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
