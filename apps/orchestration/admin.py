"""Break-glass admin for orchestration models. Operators work in the console."""

from django.contrib import admin

from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
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
    raw_id_fields = ["node", "incident"]


@admin.register(StageExecution)
class StageExecutionAdmin(admin.ModelAdmin):
    list_display = ["pipeline_run", "stage", "status", "attempt", "duration_ms", "started_at"]
    list_filter = ["stage", "status"]
    search_fields = ["pipeline_run__run_id", "pipeline_run__trace_id", "idempotency_key"]
    list_select_related = ["pipeline_run"]
    raw_id_fields = ["pipeline_run"]


@admin.register(PipelineDefinition)
class PipelineDefinitionAdmin(admin.ModelAdmin):
    list_display = ["name", "priority", "is_active", "updated_at"]
