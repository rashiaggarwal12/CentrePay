import datetime

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .engine import run_reconciliation
from .models import ReconciliationIssue, ReconciliationRun


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = ["date", "status", "started_at", "finished_at"]
    list_filter = ["status"]
    readonly_fields = ["date", "status", "started_at", "finished_at", "summary"]
    # Adds a "Run reconciliation" form above the list. Needed where no scheduler runs it
    # (free hosting without a Celery Beat worker) and no shell is available.
    change_list_template = "admin/reconciliation/reconciliationrun/change_list.html"

    def get_urls(self):
        return [
            path(
                "run/",
                self.admin_site.admin_view(require_POST(self.run_view)),
                name="reconciliation_run_now",
            ),
            *super().get_urls(),
        ]

    def run_view(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        raw = request.POST.get("date", "")
        try:
            day = datetime.date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            self.message_user(request, "Use a date like 2026-10-01.", level=messages.ERROR)
            return redirect(reverse("admin:reconciliation_reconciliationrun_changelist"))
        run = run_reconciliation(day)
        summary = run.summary
        level = messages.SUCCESS if run.status == run.Status.SUCCEEDED else messages.WARNING
        self.message_user(
            request,
            f"Reconciliation {day}: {run.status}, {summary.get('issues_total', 0)} issue(s), "
            f"{summary.get('recovered_payments', 0)} payment(s) recovered"
            + (f". Error: {summary['error']}" if summary.get("error") else "."),
            level=level,
        )
        return redirect(reverse("admin:reconciliation_reconciliationrun_change", args=[run.pk]))


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
