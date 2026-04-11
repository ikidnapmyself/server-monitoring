"""Tests for the disk analysis view."""

from pathlib import Path
from unittest.mock import patch

from django.test import Client, SimpleTestCase

from apps.intelligence.providers.base import (
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


class TestDiskAnalysisView(SimpleTestCase):
    """Tests for GET /intelligence/disk/."""

    @patch("apps.intelligence.views.disk.get_provider")
    def test_get_disk_analysis(self, mock_get_provider):
        """GET returns disk recommendations via provider.run(analysis_type='disk')."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = [
            Recommendation(
                type=RecommendationType.DISK,
                priority=RecommendationPriority.MEDIUM,
                title="Large Files",
                description="Found large files",
            )
        ]

        client = Client()
        response = client.get("/intelligence/disk/")

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "disk"
        assert data["count"] == 1
        assert data["recommendations"][0]["title"] == "Large Files"
        mock_get_provider.assert_called_once_with(
            "local", large_file_threshold_mb=100.0, old_file_days=30
        )
        mock_provider.run.assert_called_once_with(analysis_type="disk", path="/")

    @patch("apps.intelligence.views.disk.get_provider")
    def test_get_disk_analysis_custom_params(self, mock_get_provider):
        """GET with custom params passes them to get_provider."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = []

        client = Client()
        response = client.get("/intelligence/disk/?threshold_mb=50&old_days=7")

        assert response.status_code == 200
        mock_get_provider.assert_called_once_with(
            "local", large_file_threshold_mb=50.0, old_file_days=7
        )

    @patch("apps.intelligence.views.disk.get_provider")
    def test_get_disk_analysis_with_path(self, mock_get_provider):
        """GET with path param passes it to provider.run()."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = []

        client = Client()
        response = client.get("/intelligence/disk/?path=/var/log")

        assert response.status_code == 200
        mock_provider.run.assert_called_once_with(
            analysis_type="disk", path=str(Path("/var/log").resolve())
        )

    def test_get_disk_analysis_path_traversal_rejected(self):
        """GET with path traversal attempt returns 400."""
        client = Client()
        response = client.get("/intelligence/disk/?path=/../../../etc/shadow")

        assert response.status_code == 400
        data = response.json()
        assert "not allowed" in data["error"].lower()

    def test_get_disk_analysis_disallowed_path_rejected(self):
        """GET with path outside allowed roots returns 400."""
        client = Client()
        response = client.get("/intelligence/disk/?path=/root/.ssh")

        assert response.status_code == 400
        data = response.json()
        assert "not allowed" in data["error"].lower()

    @patch("apps.intelligence.views.disk.get_provider")
    def test_get_disk_analysis_allowed_root_accepted(self, mock_get_provider):
        """GET with exact allowed root path succeeds."""
        mock_provider = mock_get_provider.return_value
        mock_provider.run.return_value = []

        client = Client()
        response = client.get("/intelligence/disk/?path=/tmp")

        assert response.status_code == 200
        mock_provider.run.assert_called_once_with(
            analysis_type="disk", path=str(Path("/tmp").resolve())
        )

    @patch("apps.intelligence.views.disk.get_provider")
    def test_get_disk_analysis_provider_error(self, mock_get_provider):
        """GET returns 500 when provider raises."""
        mock_get_provider.return_value.run.side_effect = RuntimeError("disk scan failed")

        client = Client()
        response = client.get("/intelligence/disk/")

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
