"""Admin configuration for intelligence app."""

from django.contrib import admin

from apps.intelligence.models import AnalysisRun


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    """Admin for AnalysisRun model."""

    list_display = [
        "trace_id",
        "provider",
        "model_name",
        "status",
        "incident",
        "recommendations_count",
        "total_tokens",
        "duration_ms",
        "created_at",
    ]
    list_filter = ["status", "provider", "fallback_used", "created_at"]
    search_fields = ["trace_id", "pipeline_run_id", "incident__title", "summary"]
    readonly_fields = [
        "trace_id",
        "pipeline_run_id",
        "created_at",
        "started_at",
        "completed_at",
        "duration_ms",
        "total_tokens",
    ]
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident")

    fieldsets = [
        (
            "Identification",
            {"fields": ["trace_id", "pipeline_run_id", "incident"]},
        ),
        (
            "Provider",
            {"fields": ["provider", "model_name", "provider_config"]},
        ),
        (
            "Status",
            {"fields": ["status", "fallback_used"]},
        ),
        (
            "Input",
            {
                "fields": ["input_summary", "checker_output_ref"],
                "classes": ["collapse"],
            },
        ),
        (
            "Output",
            {
                "fields": [
                    "recommendations_count",
                    "recommendations",
                    "summary",
                    "probable_cause",
                    "confidence",
                ]
            },
        ),
        (
            "Token Usage",
            {
                "fields": ["prompt_tokens", "completion_tokens", "total_tokens"],
                "classes": ["collapse"],
            },
        ),
        (
            "Errors",
            {
                "fields": ["error_message"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {"fields": ["created_at", "started_at", "completed_at", "duration_ms"]},
        ),
    ]
