"""Admin configuration for alerts models."""

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.utils.html import format_html, format_html_join
from django_json_widget.widgets import JSONEditorWidget
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus, Node
from apps.alerts.reeval_existing import apply_node_alert_reeval, preview_node_alert_reeval
from apps.orchestration.models import PipelineRun
from config.dashboard import prettify_json


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
        "node",
        "incident_link",
        "started_at",
        "received_at",
    ]
    list_filter = ["status", "severity", "source", "node"]
    search_fields = [
        "name",
        "fingerprint",
        "description",
        "trace_id",
        "incident__pipeline_runs__trace_id",
    ]
    readonly_fields = [
        "fingerprint",
        "received_at",
        "updated_at",
        "journey_display",
        "pretty_labels",
        "pretty_annotations",
        "pretty_raw_payload",
    ]
    date_hierarchy = "received_at"
    inlines = [AlertHistoryInline]
    actions = ["resolve_selected"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident", "node")

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["name", "fingerprint", "source", "node", "incident"],
            },
        ),
        (
            "Status",
            {
                "fields": ["severity", "status", "description"],
            },
        ),
        (
            "Journey",
            {
                "fields": ["journey_display"],
                "description": "trace_id → incident → pipeline run.",
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

    @admin.display(description="Journey")
    def journey_display(self, obj):
        trace = format_html("<div><b>trace_id:</b> {}</div>", obj.trace_id or "—")
        if obj.incident_id:
            body = format_html(
                "<div><b>Incident:</b> "
                '<a href="/admin/alerts/incident/{}/change/">{}</a> '
                "(see its Journey for the full chain)</div>",
                obj.incident_id,
                obj.incident.title[:40],
            )
        else:
            body = format_html(
                '<div style="color:#b00;"><b>{}</b> (no incident; ingest not run)</div>',
                "not processed — inbox",
            )
        return format_html("{}{}", trace, body)

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
        "journey_display",
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
            "Journey",
            {
                "fields": ["journey_display"],
                "description": "Lifecycle: matched pipeline → run(s) → stages.",
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

    @admin.display(description="Journey")
    def journey_display(self, obj):
        if obj.pipeline_id:
            parts = [
                format_html(
                    "<div><b>Routed by:</b> {} (priority {})</div>",
                    obj.pipeline.name,
                    obj.pipeline.priority,
                )
            ]
        else:
            parts = [format_html("<div><b>Routed by:</b> {}</div>", "—")]

        runs = list(obj.pipeline_runs.all().order_by("created_at"))
        if not runs:
            parts.append(
                format_html(
                    '<div style="color:#b00;"><b>{}</b> (no pipeline run; drain with '
                    "<code>manage.py process_inbox</code>)</div>",
                    "inbox — not processed",
                )
            )
        for run in runs:
            parts.append(
                format_html(
                    '<div style="margin-top:6px;"><b>Run</b> {} — {} '
                    '<span style="color:#888;">trace {}</span></div>',
                    run.run_id,
                    run.status,
                    run.trace_id,
                )
            )
            stages = run.stage_executions.all().order_by("started_at")
            items = format_html_join(
                "",
                "<li>{} — {} ({} ms, attempt {})</li>",
                ((s.stage, s.status, f"{s.duration_ms:.0f}", s.attempt) for s in stages),
            )
            parts.append(
                format_html(
                    '<ul style="margin:2px 0 0 16px;">{}</ul>',
                    items or format_html("<li>{}</li>", "(no stages)"),
                )
            )
        # All pieces are format_html SafeStrings; join preserves escaping.
        return format_html_join("", "{}", ((p,) for p in parts))

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


@admin.register(Node)
class NodeAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Registry of agents that have pushed cluster data to this hub.

    The registry fields (instance_id, hostname, …) are written only by the
    ingest path and stay read-only. ``config`` is the one operator-editable
    field: per-checker hub-side policy used to re-evaluate alert severity
    per node (see apps/alerts/reevaluation.py).
    """

    change_actions = ["reevaluate_open_alerts"]
    list_display = ["instance_id", "hostname", "is_self", "last_source", "first_seen", "last_seen"]
    list_filter = ["is_self"]
    search_fields = ["instance_id", "hostname"]
    readonly_fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "first_seen",
        "last_seen",
    ]
    fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "config",
        "first_seen",
        "last_seen",
    ]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}
    # Numeric checkers the hub can re-evaluate, and the metric each reads:
    # cpu→cpu_percent, memory→memory_percent, disk→worst_percent,
    # disk_inodes→worst_percent, disk_temp→hottest_c, cpu_temp→hottest_c,
    # io_strain→busiest_util_percent. Example config value:
    #   {"cpu": {"warning_threshold": 99, "critical_threshold": 99}}

    def has_add_permission(self, request):
        """Nodes are written only by the ingest path, never added in admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Never delete Node rows from admin — that would silently drop the
        operator-authored ``config`` policy (a later push re-creates the row
        without it)."""
        return False

    @object_action(
        label="Re-evaluate open alerts",
        description="Re-score this node's open alerts against its current config",
    )
    def reevaluate_open_alerts(self, request, obj):
        """Preview (then, on POST confirm) re-evaluate this node's open alerts."""
        # django_object_actions gates the URL behind admin_view (is_staff only);
        # enforce model change permission before any mutation.
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        report = preview_node_alert_reeval(obj)
        if not report.changes:
            self.message_user(request, "No open alerts need re-evaluation.")
            return
        if request.method == "POST" and request.POST.get("confirm"):
            applied = apply_node_alert_reeval(obj)
            self.message_user(
                request,
                f"Resolved {applied.resolved_count}; changed severity on "
                f"{applied.severity_changed_count}.",
            )
            return
        return TemplateResponse(
            request,
            "admin/alerts/node/reevaluate_confirm.html",
            {
                **self.admin_site.each_context(request),
                "node": obj,
                "report": report,
                "title": "Confirm re-evaluation",
                "opts": self.model._meta,
            },
        )
