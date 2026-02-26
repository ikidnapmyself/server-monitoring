"""Admin configuration for intelligence app."""

from django.contrib import admin
from django.db import models as db_models
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget

from apps.intelligence.models import AnalysisRun, IntelligenceProvider
from config.admin import prettify_json


@admin.register(IntelligenceProvider)
class IntelligenceProviderAdmin(admin.ModelAdmin):
    """Admin for IntelligenceProvider model."""

    list_display = ["name", "provider", "is_active", "updated_at"]
    list_filter = ["provider", "is_active"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at"]
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}
    fieldsets = [
        (
            "General",
            {
                "fields": ["name", "provider", "is_active", "description"],
            },
        ),
        (
            "Configuration",
            {
                "fields": ["config"],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": ["created_at", "updated_at"],
            },
        ),
    ]


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
    formfield_overrides = {db_models.JSONField: {"widget": JSONEditorWidget}}
    readonly_fields = [
        "trace_id",
        "pipeline_run_id",
        "created_at",
        "started_at",
        "completed_at",
        "duration_ms",
        "total_tokens",
        "pipeline_run_link",
        "pretty_recommendations",
        "pretty_provider_config",
    ]
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("incident")

    fieldsets = [
        (
            "Identification",
            {"fields": ["trace_id", "pipeline_run_id", "pipeline_run_link", "incident"]},
        ),
        (
            "Provider",
            {"fields": ["provider", "model_name", "pretty_provider_config"]},
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
                    "pretty_recommendations",
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

    @admin.display(description="Pipeline Run")
    def pipeline_run_link(self, obj):
        if obj.pipeline_run_id:
            return format_html(
                '<a href="/admin/orchestration/pipelinerun/?q={}">View pipeline ({})</a>',
                obj.pipeline_run_id,
                obj.pipeline_run_id[:12],
            )
        return "-"

    @admin.display(description="Recommendations")
    def pretty_recommendations(self, obj):
        return prettify_json(obj.recommendations)

    @admin.display(description="Provider Config")
    def pretty_provider_config(self, obj):
        return prettify_json(obj.provider_config)
