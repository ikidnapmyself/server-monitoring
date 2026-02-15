"""Admin configuration for alerts models."""

from django.contrib import admin
from django.utils.html import format_html

from apps.alerts.models import Alert, AlertHistory, Incident
from apps.orchestration.models import PipelineRun


class AlertInline(admin.TabularInline):
    """Inline display of alerts within an incident."""

    model = Alert
    extra = 0
    readonly_fields = [
        "fingerprint",
        "source",
        "name",
        "severity",
        "status",
        "started_at",
        "received_at",
    ]
    fields = ["name", "severity", "status", "source", "started_at", "received_at"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class AlertHistoryInline(admin.TabularInline):
    """Inline display of alert history events."""

    model = AlertHistory
    extra = 0
    readonly_fields = ["event", "old_status", "new_status", "details", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PipelineRunInline(admin.TabularInline):
    """Inline display of pipeline runs for an incident."""

    model = PipelineRun
    extra = 0
    readonly_fields = [
        "run_id",
        "trace_id",
        "status",
        "current_stage",
        "created_at",
        "total_duration_ms",
    ]
    fields = ["run_id", "status", "current_stage", "created_at", "total_duration_ms"]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Admin for Alert model."""

    list_display = [
        "name",
        "severity_badge",
        "status_badge",
        "source",
        "incident_link",
        "started_at",
        "received_at",
    ]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "description"]
    readonly_fields = [
        "fingerprint",
        "received_at",
        "updated_at",
    ]
    date_hierarchy = "received_at"
    inlines = [AlertHistoryInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident")

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["name", "fingerprint", "source", "incident"],
            },
        ),
        (
            "Status",
            {
                "fields": ["severity", "status", "description"],
            },
        ),
        (
            "Metadata",
            {
                "fields": ["labels", "annotations"],
                "classes": ["collapse"],
            },
        ),
        (
            "Raw Payload",
            {
                "fields": ["raw_payload"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["started_at", "ended_at", "received_at", "updated_at"],
            },
        ),
    ]

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colors = {
            "critical": "#dc3545",
            "warning": "#ffc107",
            "info": "#17a2b8",
        }
        color = colors.get(obj.severity, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.severity.upper(),
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "firing": "#dc3545",
            "resolved": "#28a745",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.status.upper(),
        )

    @admin.display(description="Incident")
    def incident_link(self, obj):
        if obj.incident:
            return format_html(
                '<a href="/admin/alerts/incident/{}/change/">{}</a>',
                obj.incident.id,
                obj.incident.title[:30],
            )
        return "-"


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    """Admin for Incident model."""

    list_display = [
        "title",
        "severity_badge",
        "status_badge",
        "alert_count_display",
        "pipeline_runs_display",
        "created_at",
        "resolved_at",
    ]
    list_filter = ["status", "severity"]
    search_fields = ["title", "description", "summary"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "acknowledged_at",
        "resolved_at",
        "closed_at",
        "alert_count_display",
        "firing_alert_count_display",
        "pipeline_runs_display",
    ]
    date_hierarchy = "created_at"
    inlines = [AlertInline, PipelineRunInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("alerts", "pipeline_runs")

    fieldsets = [
        (
            None,
            {
                "fields": ["title", "severity", "status"],
            },
        ),
        (
            "Details",
            {
                "fields": ["description", "summary"],
            },
        ),
        (
            "Statistics",
            {
                "fields": [
                    "alert_count_display",
                    "firing_alert_count_display",
                    "pipeline_runs_display",
                ],
            },
        ),
        (
            "Metadata",
            {
                "fields": ["metadata"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                    "acknowledged_at",
                    "resolved_at",
                    "closed_at",
                ],
            },
        ),
    ]

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        colors = {
            "critical": "#dc3545",
            "warning": "#ffc107",
            "info": "#17a2b8",
        }
        color = colors.get(obj.severity, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.severity.upper(),
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colors = {
            "open": "#dc3545",
            "acknowledged": "#ffc107",
            "resolved": "#28a745",
            "closed": "#6c757d",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.status.upper(),
        )

    @admin.display(description="Alerts")
    def alert_count_display(self, obj):
        return obj.alert_count

    @admin.display(description="Firing Alerts")
    def firing_alert_count_display(self, obj):
        count = obj.firing_alert_count
        if count > 0:
            return format_html(
                '<span style="color: #dc3545; font-weight: bold;">{}</span>',
                count,
            )
        return count

    @admin.display(description="Pipeline Runs")
    def pipeline_runs_display(self, obj):
        try:
            return obj.pipeline_runs.count()
        except AttributeError:
            return "-"


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    """Admin for AlertHistory model."""

    list_display = [
        "alert",
        "event",
        "old_status",
        "new_status",
        "created_at",
    ]
    list_filter = ["event"]
    search_fields = ["alert__name", "event"]
    readonly_fields = [
        "alert",
        "event",
        "old_status",
        "new_status",
        "details",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        """Disable adding history manually - they are created programmatically."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing history - they are audit records."""
        return False
