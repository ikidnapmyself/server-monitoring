"""
URL configuration for the intelligence app.
"""

from django.urls import path

from apps.intelligence.views import (
    DiskAnalysisView,
    HealthView,
    MemoryAnalysisView,
    ProvidersListView,
    RecommendationsView,
)

app_name = "intelligence"

urlpatterns = [
    # Health check
    path("health/", HealthView.as_view(), name="health"),
    # Providers
    path("providers/", ProvidersListView.as_view(), name="providers"),
    # Recommendations
    path("recommendations/", RecommendationsView.as_view(), name="recommendations"),
    # Specific analysis endpoints
    path("memory/", MemoryAnalysisView.as_view(), name="memory"),
    path("disk/", DiskAnalysisView.as_view(), name="disk"),
]
