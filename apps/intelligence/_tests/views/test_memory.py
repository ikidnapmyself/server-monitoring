"""Tests for the memory analysis view."""

from unittest.mock import patch

import pytest
from django.test import Client, SimpleTestCase

from apps.intelligence.providers.base import (
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


@pytest.mark.django_db
class TestMemoryAnalysisView(SimpleTestCase):
    """Tests for GET /intelligence/memory/."""

    @patch("apps.intelligence.views.memory.get_provider")
    def test_get_memory_analysis(self, mock_get_provider):
        """GET returns memory recommendations via provider.run(analysis_type='memory')."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.MEMORY,
                priority=RecommendationPriority.HIGH,
                title="High Memory",
                description="Memory usage high",
            )
        ]

        client = Client()
        response = client.get("/intelligence/memory/")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "memory"
        assert data["count"] == 1
        assert data["recommendations"][0]["title"] == "High Memory"
        mock_get_provider.assert_called_once_with("local", top_n_processes=10)
        mock_provider.run.assert_called_once_with(analysis_type="memory")

    @patch("apps.intelligence.views.memory.get_provider")
    def test_get_memory_analysis_custom_top_n(self, mock_get_provider):
        """GET with top_n parameter passes it to get_provider."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = []

        client = Client()
        response = client.get("/intelligence/memory/?top_n=5")

        assert response.status_code == 200
        mock_get_provider.assert_called_once_with("local", top_n_processes=5)

    @patch("apps.intelligence.views.memory.get_provider")
    def test_get_memory_analysis_provider_error(self, mock_get_provider):
        """GET returns 500 when provider raises."""
        mock_get_provider.return_value.run.side_effect = RuntimeError("analysis failed")

        client = Client()
        response = client.get("/intelligence/memory/")

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
