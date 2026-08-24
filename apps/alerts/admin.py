"""Admin configuration for alerts models."""

import json

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django_json_widget.widgets import JSONEditorWidget
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus, Node
from apps.alerts.reeval_existing import apply_node_alert_reeval, preview_node_alert_reeval
from apps.alerts.services import IncidentManager, instance_key_from_labels
from apps.alerts.timeline import build_incident_timeline
from apps.checkers.admin_charts import render_sparkline
from apps.checkers.models import CheckRun, PreflightRun
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
        "host",
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

    @admin.display(description="Host")
    def host(self, obj):
        """Machine this alert concerns, for every source — not just cluster pushes.

        ``node`` is only linked for registered cluster nodes, so fall through to the
        shared label lookup that also defines incident grouping. Returns a plain
        string deliberately: it carries no markup, and the admin escapes any
        attacker-supplied label value for us.
        """
        if obj.node_id:
            return str(obj.node)
        return instance_key_from_labels(obj.labels) or "—"

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
        "diagnosis_display",
        "journey_display",
        "journey_timeline",
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
                "fields": ["diagnosis_display", "journey_display", "journey_timeline"],
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
            IncidentManager.acknowledge(incident.id, acknowledged_by=request.user.get_username())
            count += 1
        self.message_user(request, f"{count} incident(s) acknowledged.")

    @admin.action(description="Resolve selected incidents")
    def resolve_selected(self, request, queryset):
        count = 0
        for incident in queryset.exclude(
            status__in=[IncidentStatus.RESOLVED, IncidentStatus.CLOSED]
        ):
            IncidentManager.resolve(incident.id, resolved_by=request.user.get_username())
            count += 1
        self.message_user(request, f"{count} incident(s) resolved.")

    @object_action(label="Acknowledge", description="Mark this incident as acknowledged")
    def acknowledge_incident(self, request, obj):
        if obj.status == IncidentStatus.OPEN:
            IncidentManager.acknowledge(obj.id, acknowledged_by=request.user.get_username())
            self.message_user(request, f"Incident '{obj.title}' acknowledged.")
        else:
            self.message_user(
                request, f"Cannot acknowledge — status is '{obj.status}'.", level="warning"
            )

    @object_action(label="Resolve", description="Mark this incident as resolved")
    def resolve_incident(self, request, obj):
        if obj.status not in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            IncidentManager.resolve(obj.id, resolved_by=request.user.get_username())
            self.message_user(request, f"Incident '{obj.title}' resolved.")
        else:
            self.message_user(request, f"Already {obj.status}.", level="warning")

    @object_action(label="Close", description="Mark this incident as closed")
    def close_incident(self, request, obj):
        if obj.status != IncidentStatus.CLOSED:
            IncidentManager.close(obj.id)
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

    _STAGE_LABELS = {
        "ingest": "alerts",
        "check": "checkers",
        "analyze": "intelligence",
        "notify": "notify",
    }
    # status -> (glyph, colour, label). "stalled" reads "running / stalled" so a
    # legitimately in-flight stage is not misread as stuck.
    _STATUS_RENDER = {
        "ok": ("✓", "#2e7d32", "ok"),
        "empty": ("✓→∅", "#b26a00", "empty"),
        "failed": ("✗", "#b00020", "failed"),
        "stalled": ("…", "#b26a00", "running / stalled"),
        "skipped": ("⊘", "#888", "skipped"),
        "never_ran": ("✗", "#b00020", "never ran"),
    }

    @admin.display(description="Stage diagnosis (expected vs actual)")
    def diagnosis_display(self, obj):
        """Compact expected-vs-actual stage strip. All dynamic values escaped.

        ``runs`` reads "succeeded in N/M runs" where M is the incident's total
        pipeline-run count (not attempts of this stage).
        """
        entries = diagnose_incident(obj)
        rows = format_html_join(
            "",
            '<li><b style="display:inline-block;width:90px;">{}</b>'
            '<span style="color:{};">{} {}</span>{}{}</li>',
            (
                (
                    self._STAGE_LABELS.get(e["stage"], e["stage"]),
                    self._STATUS_RENDER.get(e["status"], ("?", "#888", e["status"]))[1],
                    self._STATUS_RENDER.get(e["status"], ("?", "#888", e["status"]))[0],
                    self._STATUS_RENDER.get(e["status"], ("?", "#888", e["status"]))[2],
                    format_html(" — {}", e["detail"]) if e.get("detail") else "",
                    (
                        format_html(' <span style="color:#888;">({})</span>', e["runs"])
                        if e.get("runs")
                        else ""
                    ),
                )
                for e in entries
            ),
        )
        return format_html('<ul style="margin:0 0 0 16px;list-style:none;padding:0;">{}</ul>', rows)

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

    @admin.display(description="Merged chronological timeline")
    def journey_timeline(self, obj):
        """Merged chronological timeline of alert history, stages, and runs.

        Unlike ``journey_display`` (a per-run pipeline tree), this interleaves all
        three sources into one time-ordered list. Renders via ``format_html_join``
        so every dynamic value (event names, error messages, notify refs — all
        derived from external payloads) is HTML-escaped.
        """
        events = build_incident_timeline(obj)
        if not events:
            return format_html("<em>{}</em>", "No timeline events yet.")
        rows = format_html_join(
            "",
            '<li><span style="color:#888;">{}</span> <b>[{}]</b> {}{}</li>',
            (
                (
                    e["when"].isoformat(),
                    e["kind"],
                    e["label"],
                    format_html(" — {}", e["detail"]) if e.get("detail") else "",
                )
                for e in events
            ),
        )
        return format_html('<ol style="margin:0 0 0 16px;">{}</ol>', rows)

    @admin.display(description="Metadata")
    def pretty_metadata(self, obj):
        return prettify_json(obj.metadata)


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    """Admin for AlertHistory model."""

    list_display = [
        "alert",
        "event_label",
        "old_status",
        "new_status",
        "created_at",
    ]
    list_filter = ["event", "created_at"]
    search_fields = ["alert__name", "event"]
    readonly_fields = [
        "alert",
        "event",
        "old_status",
        "new_status",
        "details_pretty",
        "created_at",
    ]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        """Disable adding history manually - they are created programmatically."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing history - they are audit records."""
        return False

    @admin.display(description="Event", ordering="event")
    def event_label(self, obj):
        """Human-friendly event name (e.g. ``status_changed`` → ``Status Changed``)."""
        return obj.event.replace("_", " ").replace("-", " ").title()

    @admin.display(description="Details")
    def details_pretty(self, obj):
        """Pretty-printed JSON details, HTML-escaped inside a <pre> block."""
        return format_html("<pre>{}</pre>", json.dumps(obj.details, indent=2, default=str))


@admin.register(Node)
class NodeAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Registry of agents that have pushed cluster data to this hub.

    The registry fields (instance_id, hostname, …) are written only by the
    ingest path and stay read-only. ``config`` is the one operator-editable
    field: per-checker hub-side policy used to re-evaluate alert severity
    per node (see apps/alerts/reevaluation.py).
    """

    change_actions = ["reevaluate_open_alerts"]
    list_display = ["instance_id", "hostname", "last_source", "first_seen", "last_seen"]
    search_fields = ["instance_id", "hostname"]
    readonly_fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "first_seen",
        "last_seen",
        "disk_sparkline",
        "recent_pipelines",
        "latest_preflight",
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
        "disk_sparkline",
        "recent_pipelines",
        "latest_preflight",
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

    @admin.display(description="Disk usage history")
    def disk_sparkline(self, obj):
        """Inline SVG sparkline of recent ``disk`` checker ``worst_percent``.

        Points are indexed by position (oldest → newest); runs that raised an
        alert are dotted as markers. Runs with a missing or non-numeric
        ``worst_percent`` are skipped. Reuses ``render_sparkline`` (Phase 5).
        """
        runs = list(
            CheckRun.objects.filter(hostname=obj.hostname, checker_name="disk").order_by(
                "-executed_at"
            )[:50]
        )
        runs.reverse()  # most-recent 50, restored to oldest -> newest for plotting
        points = []
        marker_xs = []
        for index, run in enumerate(runs):
            worst = (run.metrics or {}).get("worst_percent")
            if not isinstance(worst, (int, float)):
                continue
            points.append((index, float(worst)))
            if run.alert_id is not None:
                marker_xs.append(index)
        if not points:
            return "No disk history."
        return render_sparkline(points, markers=marker_xs)

    @admin.display(description="Recent pipeline runs")
    def recent_pipelines(self, obj):
        """Escaped list of the node's 10 newest pipeline runs, each admin-linked."""
        runs = list(obj.pipeline_runs.order_by("-created_at")[:10])
        if not runs:
            return "No pipeline runs for this node."
        rows = format_html_join(
            "",
            '<li><a href="{}">{}</a> — {} — {} — {}</li>',
            (
                (
                    reverse("admin:orchestration_pipelinerun_change", args=[run.pk]),
                    run.run_id,
                    run.origin,
                    run.status,
                    run.created_at.isoformat(),
                )
                for run in runs
            ),
        )
        return format_html('<ul style="margin:0 0 0 16px;">{}</ul>', rows)

    @admin.display(description="Latest preflight")
    def latest_preflight(self, obj):
        """Latest preflight matched by ``instance_id`` (PreflightRun has no node FK)."""
        if not obj.instance_id:
            return "No preflight recorded."
        run = (
            PreflightRun.objects.filter(instance_id=obj.instance_id).order_by("-created_at").first()
        )
        if run is None:
            return "No preflight recorded."
        return format_html(
            "<div>{} — <b>{}</b> " "(passed {}, warnings {}, errors {})</div>",
            run.created_at.isoformat(),
            run.overall_status,
            run.passed,
            run.warnings,
            run.errors,
        )
