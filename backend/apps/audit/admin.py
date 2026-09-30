from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["at", "action", "entity_type", "entity_id", "actor"]
    list_filter = ["action", "entity_type"]
    search_fields = ["entity_id", "actor__username"]
    date_hierarchy = "at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
