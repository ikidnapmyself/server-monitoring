"""Admin configuration for orchestration models."""

from django.contrib import admin

from apps.orchestration.models import (
    InboxItem,
    PipelineDefinition,
    PipelineRun,
    StageExecution,
)


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
class PipelineDefinitionAdmin(ReadOnlyAdmin):
    """Read-only. Operators edit routes in the console."""

    list_display = ["name", "priority", "is_active", "updated_at"]


@admin.register(InboxItem)
class InboxAdmin(ReadOnlyAdmin):
    ordering = ["created_at"]
    list_display = ["run_id", "status", "origin", "node", "created_at", "updated_at"]
    list_filter = ["status", "origin", "node"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
