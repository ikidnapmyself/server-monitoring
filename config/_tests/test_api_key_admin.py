"""Tests for APIKey admin registration."""

import pytest
from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from config.models import APIKey


class APIKeyAdminTests(TestCase):
    def test_api_key_registered_in_admin(self):
        from django.contrib import admin

        assert APIKey in admin.site._registry

    def test_api_key_list_display(self):
        from django.contrib import admin

        model_admin = admin.site._registry[APIKey]
        assert "name" in model_admin.list_display
        assert "is_active" in model_admin.list_display
        assert "masked_key" in model_admin.list_display

    def test_masked_key_display(self):
        from django.contrib import admin

        key = APIKey.objects.create(name="test")
        model_admin = admin.site._registry[APIKey]
        masked = model_admin.masked_key(key)
        assert key.prefix in masked
        assert "***" in masked

    def test_ready_idempotent(self):
        """Calling ready() again doesn't double-register APIKey."""
        from django.contrib import admin

        from config.apps import ConfigAppConfig

        app = ConfigAppConfig("config", __import__("config"))
        app.ready()  # second call — should not raise
        assert APIKey in admin.site._registry


@pytest.mark.django_db
def test_the_key_admin_is_stock_and_read_only(admin_client, rf, admin_user):
    APIKey.objects.create(name="listed")
    model_admin = admin.site._registry[APIKey]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["key"]
    response = admin_client.get(reverse("admin:config_app_apikey_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


@pytest.mark.django_db
def test_the_key_admin_never_shows_the_digest(admin_client):
    key = APIKey.objects.create(name="agent")
    response = admin_client.get(reverse("admin:config_app_apikey_change", args=[key.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert key.key not in body
    assert key._raw_key not in body
