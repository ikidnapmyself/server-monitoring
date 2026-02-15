"""Admin configuration for checkers models."""

from django.contrib import admin
from django.utils.html import format_html

from apps.checkers.models import CheckRun


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    """Admin for CheckRun model."""

    list_display = [
        "checker_name",
        "hostname",
        "status_badge",
        "message_short",
        "duration_display",
        "alert_link",
        "executed_at",
    ]
    list_filter = ["status", "checker_name", "hostname"]
    search_fields = ["checker_name", "hostname", "message", "trace_id"]
    readonly_fields = [
        "checker_name",
        "hostname",
        "status",
        "message",
        "metrics",
        "error",
        "warning_threshold",
        "critical_threshold",
        "alert",
        "duration_ms",
        "executed_at",
        "trace_id",
        "pipeline_run_link",
    ]
    date_hierarchy = "executed_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("alert")

    fieldsets = [
        (
            "Check Info",
            {
                "fields": ["checker_name", "hostname", "status"],
            },
        ),
        (
            "Result",
            {
                "fields": ["message", "metrics", "error"],
            },
        ),
        (
            "Thresholds",
            {
                "fields": ["warning_threshold", "critical_threshold"],
                "classes": ["collapse"],
            },
        ),
        (
            "Alert",
            {
                "fields": ["alert"],
            },
        ),
        (
            "Execution",
            {
                "fields": ["duration_ms", "executed_at", "trace_id", "pipeline_run_link"],
            },
        ),
    ]

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "ok": "#28a745",
            "warning": "#ffc107",
            "critical": "#dc3545",
            "unknown": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.status.upper(),
        )

    @admin.display(description="Message")
    def message_short(self, obj):
        if len(obj.message) > 50:
            return obj.message[:50] + "..."
        return obj.message

    @admin.display(description="Duration")
    def duration_display(self, obj):
        if obj.duration_ms < 1000:
            return f"{obj.duration_ms:.1f}ms"
        return f"{obj.duration_ms / 1000:.2f}s"

    @admin.display(description="Alert")
    def alert_link(self, obj):
        if obj.alert:
            return format_html(
                '<a href="/admin/alerts/alert/{}/change/">Alert #{}</a>',
                obj.alert.id,
                obj.alert.id,
            )
        return "-"

    @admin.display(description="Pipeline Run")
    def pipeline_run_link(self, obj):
        if obj.trace_id:
            return format_html(
                '<a href="/admin/orchestration/pipelinerun/?q={}">View pipeline ({})</a>',
                obj.trace_id,
                obj.trace_id[:12],
            )
        return "-"

    def has_add_permission(self, request):
        """Disable adding check runs manually - they are created by running checks."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing check runs - they are audit records."""
        return False
