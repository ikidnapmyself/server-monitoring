"""Admin configuration for alerts models."""

from django.contrib import admin
from django.utils.html import format_html
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus
from apps.orchestration.models import PipelineRun
from config.admin import prettify_json


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
    search_fields = ["name", "fingerprint", "description", "incident__pipeline_runs__trace_id"]
    readonly_fields = [
        "fingerprint",
        "received_at",
        "updated_at",
        "pretty_labels",
        "pretty_annotations",
        "pretty_raw_payload",
    ]
    date_hierarchy = "received_at"
    inlines = [AlertHistoryInline]
    actions = ["resolve_selected"]

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
                "fields": ["pretty_labels", "pretty_annotations"],
                "classes": ["collapse"],
            },
        ),
        (
            "Raw Payload",
            {
                "fields": ["pretty_raw_payload"],
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

    @admin.action(description="Resolve selected alerts")
    def resolve_selected(self, request, queryset):
        updated = queryset.update(status=AlertStatus.RESOLVED)
        self.message_user(request, f"{updated} alert(s) resolved.")

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

    @admin.display(description="Labels")
    def pretty_labels(self, obj):
        return prettify_json(obj.labels)

    @admin.display(description="Annotations")
    def pretty_annotations(self, obj):
        return prettify_json(obj.annotations)

    @admin.display(description="Raw Payload")
    def pretty_raw_payload(self, obj):
        return prettify_json(obj.raw_payload)


@admin.register(Incident)
class IncidentAdmin(DjangoObjectActions, admin.ModelAdmin):
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
        "pretty_metadata",
    ]
    date_hierarchy = "created_at"
    inlines = [AlertInline, PipelineRunInline]
    actions = ["acknowledge_selected", "resolve_selected"]
    change_actions = ["acknowledge_incident", "resolve_incident", "close_incident"]

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
                "fields": ["pretty_metadata"],
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

    @admin.action(description="Acknowledge selected incidents")
    def acknowledge_selected(self, request, queryset):
        count = 0
        for incident in queryset.filter(status=IncidentStatus.OPEN):
            incident.acknowledge()
            count += 1
        self.message_user(request, f"{count} incident(s) acknowledged.")

    @admin.action(description="Resolve selected incidents")
    def resolve_selected(self, request, queryset):
        count = 0
        for incident in queryset.exclude(
            status__in=[IncidentStatus.RESOLVED, IncidentStatus.CLOSED]
        ):
            incident.resolve()
            count += 1
        self.message_user(request, f"{count} incident(s) resolved.")

    @object_action(label="Acknowledge", description="Mark this incident as acknowledged")
    def acknowledge_incident(self, request, obj):
        if obj.status == IncidentStatus.OPEN:
            obj.acknowledge()
            self.message_user(request, f"Incident '{obj.title}' acknowledged.")
        else:
            self.message_user(
                request, f"Cannot acknowledge — status is '{obj.status}'.", level="warning"
            )

    @object_action(label="Resolve", description="Mark this incident as resolved")
    def resolve_incident(self, request, obj):
        if obj.status not in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            obj.resolve()
            self.message_user(request, f"Incident '{obj.title}' resolved.")
        else:
            self.message_user(request, f"Already {obj.status}.", level="warning")

    @object_action(label="Close", description="Mark this incident as closed")
    def close_incident(self, request, obj):
        if obj.status != IncidentStatus.CLOSED:
            obj.close()
            self.message_user(request, f"Incident '{obj.title}' closed.")
        else:
            self.message_user(request, "Already closed.", level="warning")

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

    @admin.display(description="Metadata")
    def pretty_metadata(self, obj):
        return prettify_json(obj.metadata)


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
