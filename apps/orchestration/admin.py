"""Admin configuration for orchestration models."""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


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
class PipelineRunAdmin(admin.ModelAdmin):
    """Admin for PipelineRun model."""

    list_display = [
        "run_id",
        "trace_id",
        "status",
        "source",
        "current_stage",
        "total_attempts",
        "created_at",
        "total_duration_ms",
    ]
    list_filter = ["status", "source", "current_stage", "environment"]
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
    ]
    inlines = [StageExecutionInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("incident")
            .prefetch_related("stage_executions")
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
        """Render a horizontal stage flow with status indicators."""
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
                color, icon = "#28a745", "&#10003;"
            elif status == StageStatus.RUNNING:
                color, icon = "#ffc107", "&#9679;"
            elif status == StageStatus.FAILED:
                color, icon = "#dc3545", "&#10007;"
            else:
                color, icon = "#ccc", "&#9675;"
            parts.append(
                f'<span style="display:inline-block;text-align:center;margin:0 4px;">'
                f'<span style="color:{color};font-size:18px;">{icon}</span><br>'
                f'<span style="font-size:11px;">{stage_label}</span></span>'
            )
        arrow = '<span style="color:#999;margin:0 2px;">&#8594;</span>'
        return format_html(
            '<div style="display:flex;align-items:center;padding:8px 0;">{}</div>',
            mark_safe(arrow.join(parts)),
        )


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
    readonly_fields = ["started_at", "completed_at", "duration_ms"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("pipeline_run")

    fieldsets = [
        (
            "Identification",
            {"fields": ["pipeline_run", "stage", "attempt", "idempotency_key"]},
        ),
        (
            "State",
            {"fields": ["status", "input_ref", "output_ref", "output_snapshot"]},
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
        "version",
        "is_active",
        "node_count",
        "tags_display",
        "created_by",
        "updated_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "description", "created_by"]
    readonly_fields = ["version", "created_at", "updated_at"]
    ordering = ["-updated_at"]

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["name", "description", "is_active", "created_by"],
            },
        ),
        (
            "Configuration",
            {
                "fields": ["config"],
                "description": "Pipeline configuration in JSON format. See documentation for schema.",
            },
        ),
        (
            "Metadata",
            {
                "fields": ["tags", "version", "created_at", "updated_at"],
            },
        ),
    ]

    @admin.display(description="Nodes")
    def node_count(self, obj):
        """Display the number of nodes in the pipeline."""
        nodes = obj.get_nodes()
        return len(nodes)

    @admin.display(description="Tags")
    def tags_display(self, obj):
        """Display tags in a readable format."""
        if not obj.tags:
            return "-"
        tags = obj.tags
        if isinstance(tags, dict):
            return ", ".join(f"{k}={v}" for k, v in tags.items())
        return str(tags)

    def save_model(self, request, obj, form, change):
        """Increment version on save if config changed."""
        if change and "config" in form.changed_data:
            obj.version += 1
        if not obj.created_by:
            obj.created_by = request.user.username
        super().save_model(request, obj, form, change)
