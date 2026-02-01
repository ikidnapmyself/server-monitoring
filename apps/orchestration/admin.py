"""Admin configuration for orchestration models."""

from django.contrib import admin

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
    ]
    inlines = [StageExecutionInline]

    fieldsets = [
        (
            "Identification",
            {
                "fields": [
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
