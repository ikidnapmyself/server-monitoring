"""Admin configuration for orchestration models."""

from django.contrib import admin, messages
from django.db import models as db_models
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.timesince import timesince
from django_json_widget.widgets import JSONEditorWidget
from django_object_actions import DjangoObjectActions
from django_object_actions import action as object_action

from apps.orchestration import inbox
from apps.orchestration.models import (
    InboxItem,
    PipelineDefinition,
    PipelineRun,
    PipelineStatus,
    StageExecution,
)
from config.dashboard import prettify_json


class StageExecutionInline(admin.TabularInline):
    """Inline display of stage executions within a pipeline run."""

    model = StageExecution
    extra = 0
    readonly_fields = [
        "stage",
        "status",
        "attempt",
        "idempotency_key",
        "started_at",
        "completed_at",
        "duration_ms",
        "error_type",
        "error_message",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PipelineRun)
class PipelineRunAdmin(DjangoObjectActions, admin.ModelAdmin):
    """Admin for PipelineRun model."""

    list_display = [
        "run_id",
        "trace_id",
        "pipeline_flow",
        "status",
        "origin",
        "node",
        "source",
        "current_stage",
        "total_attempts",
        "created_at",
        "total_duration_ms",
    ]
    list_filter = ["status", "origin", "node", "source", "current_stage", "environment"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
    readonly_fields = [
        "run_id",
        "trace_id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "total_duration_ms",
        "pipeline_flow",
        "node_link",
        "incident_link",
    ]
    inlines = [StageExecutionInline]
    actions = ["mark_for_retry_selected"]
    change_actions = ["mark_for_retry", "mark_failed"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("incident", "node")
            .prefetch_related("stage_executions")
        )

    @admin.action(description="Mark selected for retry")
    def mark_for_retry_selected(self, request, queryset):
        count = 0
        for run in queryset.filter(status=PipelineStatus.FAILED):
            run.mark_retrying()
            count += 1
        self.message_user(request, f"{count} pipeline run(s) marked for retry.")

    @object_action(label="Mark for Retry", description="Queue this pipeline for retry")
    def mark_for_retry(self, request, obj):
        if obj.status == PipelineStatus.FAILED:
            obj.mark_retrying()
            self.message_user(request, f"Pipeline '{obj.run_id}' marked for retry.")
        else:
            self.message_user(
                request,
                f"Can only retry failed pipelines (current: {obj.status}).",
                level="warning",
            )

    @object_action(label="Mark Failed", description="Mark this pipeline as failed")
    def mark_failed(self, request, obj):
        if obj.status not in (PipelineStatus.FAILED, PipelineStatus.NOTIFIED):
            obj.mark_failed(
                error_type="ManualOverride",
                message="Manually marked as failed via admin",
            )
            self.message_user(request, f"Pipeline '{obj.run_id}' marked as failed.")
        else:
            self.message_user(
                request,
                f"Cannot mark as failed — status is '{obj.status}'.",
                level="warning",
            )

    fieldsets = [
        (
            "Identification",
            {
                "fields": [
                    "pipeline_flow",
                    "trace_id",
                    "run_id",
                    "incident",
                    "incident_link",
                    "node_link",
                    "source",
                    "environment",
                    "alert_fingerprint",
                ]
            },
        ),
        (
            "State",
            {
                "fields": [
                    "status",
                    "current_stage",
                    "total_attempts",
                    "max_retries",
                ]
            },
        ),
        (
            "References",
            {
                "fields": [
                    "normalized_payload_ref",
                    "checker_output_ref",
                    "intelligence_output_ref",
                    "notify_output_ref",
                    "intelligence_fallback_used",
                ]
            },
        ),
        (
            "Errors",
            {
                "fields": [
                    "last_error_type",
                    "last_error_message",
                    "last_error_retryable",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                    "started_at",
                    "completed_at",
                    "total_duration_ms",
                ]
            },
        ),
    ]

    @admin.display(description="Pipeline Flow")
    def pipeline_flow(self, obj):
        """Render a horizontal stage flow with status indicators.

        Warning: This method calls obj.stage_executions.all() and should NOT be
        added to list_display as it would cause N+1 query problems. Use only in
        readonly_fields and detail view fieldsets where prefetch_related is effective.
        """
        from apps.orchestration.models import PipelineStage, StageStatus

        stages = [
            (PipelineStage.INGEST, "INGEST"),
            (PipelineStage.CHECK, "CHECK"),
            (PipelineStage.ANALYZE, "ANALYZE"),
            (PipelineStage.NOTIFY, "NOTIFY"),
        ]
        executions = {se.stage: se.status for se in obj.stage_executions.all()}
        parts = []
        for stage_value, stage_label in stages:
            status = executions.get(stage_value, None)
            if status == StageStatus.SUCCEEDED:
                color, icon = "#28a745", "✓"
            elif status == StageStatus.RUNNING:
                color, icon = "#ffc107", "●"
            elif status == StageStatus.FAILED:
                color, icon = "#dc3545", "✗"
            else:
                color, icon = "#ccc", "○"
            # Build each stage part with format_html for proper escaping of dynamic content
            part = format_html(
                '<span style="display:inline-block;text-align:center;margin:0 4px;">'
                '<span style="color:{};font-size:18px;">{}</span><br>'
                '<span style="font-size:11px;">{}</span></span>',
                color,
                icon,
                stage_label,
            )
            parts.append(part)

        # Join parts with format_html_join — Django's safe way to join HTML fragments.
        # Each part is already a SafeString from format_html above.
        # The separator uses format_html with a placeholder to avoid the no-args deprecation.
        separator = format_html(
            '<span style="color:#999;margin:0 2px;">{}</span>',
            "\u2192",
        )
        stages_html = format_html_join(separator, "{}", ((part,) for part in parts))

        return format_html(
            '<div style="display:flex;align-items:center;padding:8px 0;">{}</div>',
            stages_html,
        )

    @admin.display(description="Node")
    def node_link(self, obj):
        """Link to the Node change page this run concerns (— when unset)."""
        if obj.node_id:
            return format_html(
                '<a href="{}">{}</a>',
                reverse("admin:alerts_node_change", args=[obj.node_id]),
                str(obj.node),
            )
        return "—"

    @admin.display(description="Incident")
    def incident_link(self, obj):
        """Link to the linked Incident change page (— when unset)."""
        if obj.incident_id:
            return format_html(
                '<a href="{}">{}</a>',
                reverse("admin:alerts_incident_change", args=[obj.incident_id]),
                str(obj.incident),
            )
        return "—"


@admin.register(StageExecution)
class StageExecutionAdmin(admin.ModelAdmin):
    """Admin for StageExecution model."""

    list_display = [
        "pipeline_run",
        "stage",
        "status",
        "attempt",
        "duration_ms",
        "started_at",
    ]
    list_filter = ["stage", "status"]
    search_fields = ["pipeline_run__run_id", "pipeline_run__trace_id", "idempotency_key"]
    readonly_fields = ["started_at", "completed_at", "duration_ms", "pretty_output_snapshot"]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("pipeline_run")

    @admin.display(description="Output Snapshot")
    def pretty_output_snapshot(self, obj):
        return prettify_json(obj.output_snapshot)

    fieldsets = [
        (
            "Identification",
            {"fields": ["pipeline_run", "stage", "attempt", "idempotency_key"]},
        ),
        (
            "State",
            {"fields": ["status", "input_ref", "output_ref", "pretty_output_snapshot"]},
        ),
        (
            "Errors",
            {
                "fields": [
                    "error_type",
                    "error_message",
                    "error_stack",
                    "error_retryable",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {"fields": ["started_at", "completed_at", "duration_ms"]},
        ),
    ]


@admin.register(PipelineDefinition)
class PipelineDefinitionAdmin(admin.ModelAdmin):
    """Admin for PipelineDefinition model."""

    list_display = [
        "name",
        "priority",
        "is_active",
        # One compact, join-free column that shows a lane's whole routing decision —
        # something three separate booleans could never do at a glance.
        "stages",
        "channel_name",
        "created_by",
        "updated_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "description", "created_by"]
    readonly_fields = ["version", "created_at", "updated_at"]
    ordering = ["priority", "-updated_at"]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["name", "description", "is_active", "created_by"],
            },
        ),
        (
            "Routing",
            {
                "fields": [
                    "match",
                    "priority",
                    "stages",
                    "channel",
                ],
                # origin's checker_generated value cannot match a lane yet: it is
                # only set for checks_only runs, which take the CHECK-only branch in
                # the orchestrator and never reach routing. Task 8 wires that up.
                # Don't promise lanes can separate hub checks from inbound traffic.
                "description": (
                    "match: [{field, op, value}] (field: source|severity|instance|origin|"
                    "label:<k>; op: is|is-not|in|not-in). origin is where the run started: "
                    "incoming_webhook | checker_generated | manual. Facts come from "
                    "the run's subject alert. Empty match = catch-all. Lower priority is "
                    "evaluated first; first match wins. stages is the ordered downstream "
                    'list, e.g. ["check", "analyze", "notify"] — the entry stage is already '
                    "done by the time this lane is picked, so it is not listed. channel is "
                    "the single notify target — delivery has never fanned out."
                ),
            },
        ),
        (
            "Metadata",
            {
                "fields": ["tags", "version", "created_at", "updated_at"],
            },
        ),
    ]

    def get_queryset(self, request):
        """Join the channel so the changelist's channel_name avoids an N+1."""
        return super().get_queryset(request).select_related("channel")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Pre-fill a new lane's ``stages`` with the full downstream pipeline.

        The model default stays ``[]`` so an ingest-only lane remains expressible
        without a sentinel. But a blank Add form is a footgun: an operator who fills
        in ``match``, attaches a channel and leaves ``stages`` alone would create a
        lane that swallows every matching incident and delivers nothing. Seeding the
        *form* makes the common case the path of least resistance while leaving
        "empty means empty" intact in the data model. On a change form the instance's
        own value wins, so an explicitly emptied list is not silently refilled.
        """
        if db_field.name == "stages":
            kwargs.setdefault("initial", list(PipelineDefinition.ROUTABLE_STAGES))
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="Channel", ordering="channel__name")
    def channel_name(self, obj):
        """Name of the notify channel this lane targets, or an em dash when unset.

        A deactivated channel is marked, because ``_route_incident`` treats it as no
        channel at all: delivery silently falls back to payload-driven selection. A
        bare name here would tell an operator the lane routes when it does not — the
        same "config that lies" this field's own FK refactor was meant to end.
        """
        if obj.channel is None:
            return "—"
        if obj.routed_channel() is not None:
            return obj.channel.name
        return format_html(
            '{} <span style="color:#999;font-size:11px;">(inactive)</span>',
            obj.channel.name,
        )

    def save_model(self, request, obj, form, change):
        """Default created_by to the acting user."""
        if not obj.created_by:
            obj.created_by = request.user.username
        super().save_model(request, obj, form, change)


@admin.register(InboxItem)
class InboxAdmin(admin.ModelAdmin):
    """Monitor of un-drained pipeline runs (PENDING/PROCESSING) with drain/reclaim.

    Reuses the shared ``apps.orchestration.inbox`` helpers so the drain/claim logic
    is single-sourced with the ``process_inbox`` management command.
    """

    ordering = ["created_at"]  # oldest first — the natural drain order
    list_display = ["run_id", "source", "node", "origin", "status", "age", "stuck"]
    list_filter = ["status", "origin", "node", "source"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
    actions = ["drain_selected", "reclaim_stuck"]

    # Draining runs the full pipeline synchronously per row inside the request, so a
    # large selection would tie up the worker. Refuse anything bigger and let the
    # operator narrow the selection.
    max_drain_selection = 25

    def has_add_permission(self, request):
        # The inbox is populated by ingest, never created by hand.
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("node")

    @admin.display(description="Age")
    def age(self, obj):
        """Human-readable time since the run was recorded."""
        return timesince(obj.created_at)

    @admin.display(description="Stuck", boolean=True)
    def stuck(self, obj):
        """True if the run has been PROCESSING past the stall timeout."""
        return obj.is_stuck()

    @admin.action(description="Drain selected (process now)")
    def drain_selected(self, request, queryset):
        """Claim + execute each selected run via the shared inbox helper.

        Each row is isolated: a failure (including a row claimed or deleted between
        listing and action, which raises ``PipelineRun.DoesNotExist``) is counted and
        the drain continues. Oversized selections are refused up front because each row
        runs the full pipeline synchronously in-request.
        """
        if queryset.count() > self.max_drain_selection:
            self.message_user(
                request,
                f"Refusing to drain more than {self.max_drain_selection} runs at once; "
                "narrow the selection and retry.",
                level=messages.WARNING,
            )
            return

        processed = 0
        failed = 0
        for obj in queryset:
            try:
                processed += inbox.drain_run(obj.run_id)
            except (
                Exception
            ):  # noqa: BLE001 — isolate each row; one bad run must not abort the rest
                failed += 1
        if failed:
            self.message_user(
                request,
                f"Drained {processed} run(s); {failed} failed.",
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                f"Drained {processed} run(s).",
                level=messages.SUCCESS,
            )

    @admin.action(description="Reclaim stuck (PROCESSING -> PENDING)")
    def reclaim_stuck(self, request, queryset):
        """Return stalled PROCESSING runs in the selection to PENDING (scoped reclaim)."""
        pks = list(queryset.values_list("pk", flat=True))
        count = inbox.reclaim_stuck(pks=pks)
        self.message_user(request, f"Reclaimed {count} stuck run(s).", level=messages.SUCCESS)
