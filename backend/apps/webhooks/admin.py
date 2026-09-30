from django.contrib import admin

from .models import WebhookEvent
from .tasks import process_webhook_event


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["received_at", "event_type", "gateway_event_id", "processed_at", "attempts"]
    list_filter = ["event_type", ("processed_at", admin.EmptyFieldListFilter)]
    search_fields = ["gateway_event_id"]
    date_hierarchy = "received_at"
    readonly_fields = [
        "gateway_event_id",
        "event_type",
        "raw_payload",
        "received_at",
        "processed_at",
        "attempts",
        "last_error",
    ]
    actions = ["replay"]

    @admin.action(description="Replay selected events")
    def replay(self, request, queryset):
        for pk in queryset.values_list("pk", flat=True):
            process_webhook_event.delay(pk, force=True)
        self.message_user(request, f"Queued {queryset.count()} event(s) for replay.")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
