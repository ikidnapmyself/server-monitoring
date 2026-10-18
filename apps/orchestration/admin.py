"""Admin configuration for orchestration models."""

from django.contrib import admin
from django.db import models as db_models
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget

from apps.notify.models import NotificationChannel
from apps.orchestration.models import (
    InboxItem,
    PipelineDefinition,
    PipelineRun,
    StageExecution,
)
from config.admin_badges import tinted
from config.admin_links import DASH, admin_link, changelist_link


class ReadOnlyAdmin(admin.ModelAdmin):
    """Read-only. Operators act on runs and the inbox in the console."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PipelineRun)
class PipelineRunAdmin(ReadOnlyAdmin):
    list_display = [
        "run_id",
        "status",
        "origin",
        "node",
        "incident",
        "current_stage",
        "total_attempts",
        "created_at",
    ]
    list_filter = ["status", "origin", "node", "source", "current_stage", "environment"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
    list_select_related = ["node", "incident"]


@admin.register(StageExecution)
class StageExecutionAdmin(ReadOnlyAdmin):
    list_display = ["pipeline_run", "stage", "status", "attempt", "duration_ms", "started_at"]
    list_filter = ["stage", "status"]
    search_fields = ["pipeline_run__run_id", "pipeline_run__trace_id", "idempotency_key"]
    list_select_related = ["pipeline_run"]


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
                "description": (
                    "match: [{field, op, value}] (field: source|severity|status|instance|"
                    "origin|label:<k>; op: is|is-not|in|not-in). status is firing or "
                    "resolved. origin is where the run started: "
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
            return changelist_link(NotificationChannel, DASH)
        link = admin_link(obj.channel, obj.channel.name)
        if obj.routed_channel() is not None:
            return link
        return format_html(
            '{} <span class="ops-note">{}</span>',
            link,
            tinted("(inactive)", "muted"),
        )

    def save_model(self, request, obj, form, change):
        """Default created_by to the acting user."""
        if not obj.created_by:
            obj.created_by = request.user.username
        super().save_model(request, obj, form, change)


@admin.register(InboxItem)
class InboxAdmin(ReadOnlyAdmin):
    ordering = ["created_at"]
    list_display = ["run_id", "status", "origin", "node", "created_at", "updated_at"]
    list_filter = ["status", "origin", "node"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
