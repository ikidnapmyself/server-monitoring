"""Tests for the health view."""

import pytest
from django.test import Client, SimpleTestCase


@pytest.mark.django_db
class TestHealthView(SimpleTestCase):
    """Tests for HealthView."""

    def test_health_returns_ok(self):
        """Test health endpoint returns healthy status."""
        client = Client()
        response = client.get("/intelligence/health/")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["app"] == "intelligence"
        assert "providers" in data
