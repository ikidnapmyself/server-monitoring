"""Tests for APIKey admin registration."""

from django.test import TestCase

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
        assert key.key[:8] in masked
        assert "***" in masked
