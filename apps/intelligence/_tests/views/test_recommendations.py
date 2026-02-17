"""Tests for the recommendations view."""

import json
from unittest.mock import patch

import pytest
from django.test import Client

from apps.intelligence.providers.base import (
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)

SAMPLE_RECOMMENDATIONS = [
    Recommendation(
        type=RecommendationType.MEMORY,
        priority=RecommendationPriority.HIGH,
        title="High Memory",
        description="Memory usage high",
        actions=["Restart service"],
    )
]


@pytest.mark.django_db
class TestRecommendationsGetView:
    """Tests for GET /intelligence/recommendations/."""

    @patch("apps.intelligence.views.recommendations.get_provider")
    def test_get_recommendations_without_incident(self, mock_get_provider):
        """GET without incident_id calls provider.run() (no incident)."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = SAMPLE_RECOMMENDATIONS

        client = Client()
        response = client.get("/intelligence/recommendations/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["recommendations"][0]["title"] == "High Memory"
        mock_provider.run.assert_called_once_with()

    @patch("apps.intelligence.views.recommendations.get_provider")
    def test_get_recommendations_with_incident(self, mock_get_provider):
        """GET with incident_id calls provider.run(incident=...)."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        incident = Incident.objects.create(
            title="Test Incident",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )

        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = SAMPLE_RECOMMENDATIONS

        client = Client()
        response = client.get(f"/intelligence/recommendations/?incident_id={incident.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        mock_provider.run.assert_called_once_with(incident=incident)

    @patch("apps.intelligence.views.recommendations.get_provider")
    def test_get_recommendations_incident_not_found(self, mock_get_provider):
        """GET with non-existent incident_id returns 404."""
        client = Client()
        response = client.get("/intelligence/recommendations/?incident_id=99999")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["error"]


@pytest.mark.django_db
class TestRecommendationsPostView:
    """Tests for POST /intelligence/recommendations/."""

    @patch("apps.intelligence.views.recommendations.get_provider")
    def test_post_recommendations_without_incident(self, mock_get_provider):
        """POST without incident_id calls provider.run(provider_config=...)."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = SAMPLE_RECOMMENDATIONS

        client = Client()
        response = client.post(
            "/intelligence/recommendations/",
            data=json.dumps({"provider": "local", "config": {"top_n_processes": 5}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        mock_provider.run.assert_called_once_with(provider_config={"top_n_processes": 5})

    @patch("apps.intelligence.views.recommendations.get_provider")
    def test_post_recommendations_with_incident(self, mock_get_provider):
        """POST with incident_id calls provider.run(incident=..., provider_config=...)."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        incident = Incident.objects.create(
            title="Test Incident",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )

        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = SAMPLE_RECOMMENDATIONS

        client = Client()
        response = client.post(
            "/intelligence/recommendations/",
            data=json.dumps({"incident_id": incident.id, "config": {}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        mock_provider.run.assert_called_once_with(incident=incident, provider_config={})

    def test_post_recommendations_invalid_json(self):
        """POST with invalid JSON returns 400."""
        client = Client()
        response = client.post(
            "/intelligence/recommendations/",
            data="not json",
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert "Invalid JSON" in data["error"]
