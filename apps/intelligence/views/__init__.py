"""
Intelligence app views.

This package contains HTTP endpoints for intelligence recommendations.
Views are organized by endpoint/functionality.
"""

from apps.intelligence.views.disk import DiskAnalysisView
from apps.intelligence.views.health import HealthView
from apps.intelligence.views.memory import MemoryAnalysisView
from apps.intelligence.views.providers import ProvidersListView
from apps.intelligence.views.recommendations import RecommendationsView

__all__ = [
    "DiskAnalysisView",
    "HealthView",
    "MemoryAnalysisView",
    "ProvidersListView",
    "RecommendationsView",
]
