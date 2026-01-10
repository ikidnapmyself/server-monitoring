"""URL configuration for the orchestration app."""

from django.urls import path

from apps.orchestration.views import (
    PipelineListView,
    PipelineResumeView,
    PipelineStatusView,
    PipelineView,
)

app_name = "orchestration"

urlpatterns = [
    # Pipeline trigger endpoints
    path("pipeline/", PipelineView.as_view(), name="pipeline-trigger"),
    path("pipeline/sync/", PipelineView.as_view(), {"mode": "sync"}, name="pipeline-trigger-sync"),
    # Pipeline status/listing endpoints
    path("pipelines/", PipelineListView.as_view(), name="pipeline-list"),
    path("pipeline/<str:run_id>/", PipelineStatusView.as_view(), name="pipeline-status"),
    path("pipeline/<str:run_id>/resume/", PipelineResumeView.as_view(), name="pipeline-resume"),
]
