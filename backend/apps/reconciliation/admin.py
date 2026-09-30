from django.contrib import admin, messages
from django.utils import timezone

from .models import ReconciliationIssue, ReconciliationRun


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ["date", "status", "started_at", "finished_at"]
    list_filter = ["status"]
    readonly_fields = ["date", "status", "started_at", "finished_at", "summary"]


@admin.register(ReconciliationIssue)
class ReconciliationIssueAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "kind",
        "status",
        "gateway_ref",
        "invoice",
        "expected_paise",
        "actual_paise",
    ]
    list_filter = ["status", "kind"]
    search_fields = ["gateway_ref", "local_ref", "invoice__number"]
    readonly_fields = [
        "run",
        "kind",
        "gateway_ref",
        "local_ref",
        "invoice",
        "expected_paise",
        "actual_paise",
        "details",
        "status",
        "resolved_by",
        "resolved_at",
        "created_at",
    ]
    fields = readonly_fields[:8] + ["resolution_note"] + readonly_fields[8:]
    actions = ["resolve"]

    @admin.action(description="Resolve selected issues (uses the resolution note)")
    def resolve(self, request, queryset):
        open_issues = queryset.filter(status=ReconciliationIssue.Status.OPEN)
        missing_note = open_issues.filter(resolution_note="").count()
        if missing_note:
            self.message_user(
                request,
                f"{missing_note} issue(s) have no resolution note. Open each one, add a note, "
                "save, then resolve.",
                level=messages.ERROR,
            )
            return
        count = open_issues.update(
            status=ReconciliationIssue.Status.RESOLVED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
        )
        self.message_user(request, f"Resolved {count} issue(s).")
