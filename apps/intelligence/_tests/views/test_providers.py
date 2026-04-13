"""Tests for the providers list view."""

import pytest
from django.test import Client, SimpleTestCase, override_settings


@pytest.mark.django_db
@override_settings(API_KEY_AUTH_ENABLED=False)
class TestProvidersListView(SimpleTestCase):
    """Tests for ProvidersListView."""

    def test_list_providers(self):
        """Test providers list endpoint."""
        client = Client()
        response = client.get("/intelligence/providers/")

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "count" in data
        assert "local" in data["providers"]
