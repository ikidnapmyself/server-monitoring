"""Admin configuration for alerts models."""

import json

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.forms import ADD_SECTION_FIELD, NodePolicyForm
from apps.alerts.models import (
    Alert,
    AlertHistory,
    AlertStatus,
    Incident,
    IncidentStatus,
    Node,
)
from apps.alerts.node_overview import (
    SEVERITIES_WORST_FIRST,
    SEVERITY_COLORS,
    UNRESOLVED_INCIDENT_STATUSES,
    build_node_overview,
    render_severity_chips,
)
from apps.alerts.node_policy import (
    addable_checkers,
    build_effective_policy,
    field_name,
    scoring_changed,
    sections_for,
    spec_for,
)
from apps.alerts.reeval_existing import apply_node_alert_reeval, preview_node_alert_reeval
from apps.alerts.services import IncidentManager, instance_key_from_labels
from apps.alerts.timeline import build_incident_timeline
from apps.orchestration.models import PipelineRun
from config.dashboard import prettify_json


def node_label(node) -> str:
    """One name for a machine, for changelist columns.

    ``Node.__str__`` renders ``instance_id (hostname)``, but the two are the same
    string on most installs, so the pair reads as noise in a list. Prefer the
    hostname and fall back to the instance_id. The change page and the Node admin
    still show both.
    """
    return node.hostname or node.instance_id


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
        color = SEVERITY_COLORS.get(obj.severity, "#6c757d")
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
            return node_label(obj.node)
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
        "host",
        "severity_badge",
        "status_badge",
        "alert_count_display",
        "pipeline_runs_display",
        "created_at",
        "resolved_at",
    ]
    list_filter = ["status", "severity", "alerts__node"]
    search_fields = [
        "title",
        "description",
        "summary",
        "alerts__node__instance_id",
        "alerts__node__hostname",
    ]
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
        return (
            super()
            .get_queryset(request)
            .distinct()
            .prefetch_related("alerts__node", "pipeline_runs")
        )

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

    @admin.display(description="Host")
    def host(self, obj):
        """Machines this incident is about, read off its alerts.

        An incident groups by alert name *and* instance (see
        ``incident_instance_key``), so this is normally one machine. Multiple values
        only show up for legacy rows grouped before that rule existed. Prefers the
        registered ``Node`` and falls back to the same label lookup that defines the
        grouping. Returns a plain string deliberately: it carries no markup, and the
        admin escapes any attacker-supplied label value for us.
        """
        seen = []
        for alert in obj.alerts.all():
            name = (
                node_label(alert.node) if alert.node_id else instance_key_from_labels(alert.labels)
            )
            if name and name not in seen:
                seen.append(name)
        return ", ".join(seen) or "—"

    @admin.display(description="Severity")
    def severity_badge(self, obj):
        color = SEVERITY_COLORS.get(obj.severity, "#6c757d")
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
    ingest path and stay read-only. The one thing an operator edits is
    ``config``, the per-checker hub-side policy used to re-evaluate alert
    severity per node (see apps/alerts/reevaluation.py), and it is edited
    through ``NodePolicyForm``'s labelled boxes rather than as raw JSON.
    """

    change_actions = ["reevaluate_open_alerts"]
    list_display = [
        "instance_id",
        "hostname",
        "incidents",
        "last_source",
        "first_seen",
        "last_seen",
    ]
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
    # Registry only. ``config`` is deliberately absent: it is edited through
    # ``NodePolicyForm``'s per-checker boxes, and a raw JSON widget alongside
    # them would be a second writer for one column, where whichever renders last
    # silently wins.
    fields = [
        "instance_id",
        "hostname",
        "address",
        "last_source",
        "labels",
        "first_seen",
        "last_seen",
    ]
    form = NodePolicyForm
    change_form_template = "admin/alerts/node/change_form.html"

    def get_form(self, request, obj=None, change=False, **kwargs):
        """Let the form own its field list.

        ``_changeform_view`` flattens the fieldsets into
        ``modelform_factory(fields=...)``, and the policy boxes are not model
        fields, so that raises ``FieldError``. It passes ``fields`` explicitly,
        so this overrides rather than defaults. Empty rather than ``None``,
        because ``get_form`` extends ``exclude`` by this list for a staff user
        with view-but-not-change permission, and extending by ``None`` raises.
        No model field is lost either way: every one here is read-only or is
        ``config``, which ``NodePolicyForm`` owns.
        """
        kwargs["fields"] = []
        return super().get_form(request, obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        """The registry above, then one labelled box per checker with a policy.

        The sections come from ``sections_for`` rather than from a hand-written
        list, so a node shows the checkers it actually reports and adding a
        scorer adds a section with no edit here. Fields built in the form's
        ``__init__`` never reach ``base_fields``, so the admin cannot discover
        them on its own.

        The select that adds a section goes last, below the sections it grows,
        rather than up in the registry where it would read as another read-only
        registry fact. It is omitted, exactly as the form omits it, when every
        checker already has a section: a select that can only be left blank is
        a control an operator has to work out is useless.
        """
        registry = [(None, {"fields": self.fields})]
        if obj is None or not self.has_change_permission(request, obj):
            # A reader who cannot change gets every fieldset field rendered
            # read-only, and ``AdminReadonlyField`` has no value to read for a
            # field the form adds in ``__init__``, so the boxes came out as
            # "None" directly below a panel saying "Warning at 80". The panel
            # from ``build_effective_policy`` is that reader's whole answer and
            # it is already complete, so the boxes are simply not offered.
            return registry
        sections = sections_for(obj)
        fieldsets = registry + [
            (
                f"{checker.replace('_', ' ')} policy",
                {"fields": [field_name(checker, field.name) for field in spec_for(checker)]},
            )
            for checker in sections
        ]
        if addable_checkers(sections):
            fieldsets.append(
                (
                    "Add a policy section",
                    {
                        "fields": [ADD_SECTION_FIELD],
                        "description": "Set a threshold for a checker this node has not "
                        "reported yet. Saving opens its boxes and changes no scoring.",
                    },
                )
            )
        return fieldsets

    def get_queryset(self, request):
        """Annotate unresolved incident counts, one per severity, in one query.

        ``distinct=True`` carries the weight here: an incident this node raised
        six alerts on is one incident, not six. Every aggregate rides the same
        ``alerts__incident`` join, so listing the whole fleet stays one query.
        """
        annotations = {
            f"unresolved_{severity}": Count(
                "alerts__incident",
                distinct=True,
                filter=Q(
                    alerts__incident__status__in=UNRESOLVED_INCIDENT_STATUSES,
                    alerts__incident__severity=severity,
                ),
            )
            for severity in SEVERITIES_WORST_FIRST
        }
        annotations["unresolved_total"] = Count(
            "alerts__incident",
            distinct=True,
            filter=Q(alerts__incident__status__in=UNRESOLVED_INCIDENT_STATUSES),
        )
        return super().get_queryset(request).annotate(**annotations)

    @admin.display(description="Incidents", ordering="unresolved_total")
    def incidents(self, obj):
        """Unresolved incident counts for this node, worst severity first."""
        return render_severity_chips(obj)

    def has_add_permission(self, request):
        """Nodes are written by code, never added in admin.

        Two writers: the ingest path (a cluster push registers its sender) and
        ``CheckAlertBridge`` (a local check run registers this machine).
        """
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

    # Django's own response_change branches on these before falling through to
    # the changelist. Any one of them means the operator asked to go somewhere
    # specific, so the redirect below leaves them alone.
    _EXPLICIT_DESTINATIONS = ["_continue", "_saveasnew", "_addanother", "_popup"]

    def save_model(self, request, obj, form, change):
        """Save, and remember whether the save can re-score anything.

        The verdict has to be taken here. ``ModelAdmin._changeform_view`` calls
        ``form.save(commit=False)`` before this, so ``obj.config`` already holds
        the new policy, and the stored one only exists in the database. It is
        carried on the request rather than on ``self``: a ModelAdmin is one
        shared instance serving every request.
        """
        stored = self.model.objects.filter(pk=obj.pk).values_list("config", flat=True).first()
        request.node_policy_scoring_changed = scoring_changed(stored, obj.config)
        # Carried the same way and for the same reason: ``response_change`` needs
        # to know the operator asked for boxes, and only the form saw the select.
        request.node_policy_section_added = form.cleaned_data.get(ADD_SECTION_FIELD) or ""
        super().save_model(request, obj, form, change)

    def response_change(self, request, obj):
        """A save that changed scoring lands on the re-evaluate preview.

        Saving a threshold does nothing to the alerts already open; that takes a
        second, easily forgotten click, and a policy nobody re-evaluates is the
        same silent no-op the boxes above exist to kill. So the save hands the
        operator the preview rather than the changelist.

        Still two deliberate acts: this is the action's GET, which shows what
        would change and waits for the confirm. It must never apply the
        re-evaluation, because a fat-fingered threshold that resolves real
        incidents with no preview is worse than the button nobody presses.

        A save that only opened a section lands back on the boxes it opened,
        because "show me those boxes" is the whole content of that request and
        the changelist means finding the node again to use what you just asked
        for. An empty entry scores nothing, so one save can ask for both and the
        preview above takes it: that is the half with consequences for alerts
        already open, and the boxes are one click away either way.

        Only the plain Save is redirected. ``_continue`` and friends mean the
        operator said where they were going, and ``super`` still owns every
        other case, including an invalid form.
        """
        response = super().response_change(request, obj)
        if any(key in request.POST for key in self._EXPLICIT_DESTINATIONS):
            return response
        if getattr(request, "node_policy_scoring_changed", False):
            self.message_user(
                request,
                "This policy does not touch the alerts already open. Here is what "
                "re-evaluating them would do.",
            )
            # tools_view_name is django_object_actions' own name for the URL it
            # registers for the change actions, so the button on the page and this
            # redirect cannot drift apart.
            return HttpResponseRedirect(
                reverse(
                    self.tools_view_name,
                    kwargs={"pk": obj.pk, "tool": "reevaluate_open_alerts"},
                )
            )
        added = getattr(request, "node_policy_section_added", "")
        if added:
            # One save can do both. The preview above wins: it is the half with
            # consequences for alerts already open, and the boxes are one click
            # away and score nothing until they are filled in.
            self.message_user(request, f"Opened the {added.replace('_', ' ')} policy boxes.")
            return HttpResponseRedirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[obj.pk],
                    current_app=self.admin_site.name,
                )
            )
        return response

    def render_change_form(self, request, context, *args, obj=None, **kwargs):
        """Attach the overview panels; the form below is unchanged.

        ``obj`` is None on the add view, which NodeAdmin forbids anyway; the
        guard costs nothing and the template's ``{% if node_overview %}``
        depends on it.
        """
        if obj is not None:
            context["node_overview"] = build_node_overview(obj)
            # Rendered for every reader, not only the view-only one. An operator
            # editing thresholds needs "what is scoring today" stated apart from
            # the boxes that will change it, and a panel that appears for some
            # users is a condition that has to stay right.
            context["node_policy"] = build_effective_policy(obj)
        return super().render_change_form(request, context, *args, obj=obj, **kwargs)
