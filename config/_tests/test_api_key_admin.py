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
        assert key.prefix in masked
        assert "***" in masked

    def test_save_model_flashes_raw_token_once_on_create(self):
        from unittest.mock import MagicMock, patch

        from django.contrib import admin

        model_admin = admin.site._registry[APIKey]
        request = MagicMock()
        obj = APIKey(name="agent web-03")
        with patch("config.admin.messages") as messages:
            model_admin.save_model(request, obj, form=MagicMock(), change=False)
            # The raw token (40 hex chars) is surfaced exactly once via messages.
            flashed = " ".join(str(c) for c in messages.mock_calls)
            assert obj._raw_key in flashed
            # The stored digest must never be surfaced.
            assert obj.key not in flashed

    def test_save_model_no_token_on_edit(self):
        from unittest.mock import MagicMock, patch

        from django.contrib import admin

        model_admin = admin.site._registry[APIKey]
        obj = APIKey.objects.create(name="existing")
        request = MagicMock()
        with patch("config.admin.messages") as messages:
            model_admin.save_model(request, obj, form=MagicMock(), change=True)
            assert not any("Raw token" in str(c) for c in messages.mock_calls)

    def test_ready_idempotent(self):
        """Calling ready() again doesn't double-register APIKey."""
        from django.contrib import admin

        from config.apps import ConfigAppConfig

        app = ConfigAppConfig("config", __import__("config"))
        app.ready()  # second call — should not raise
        assert APIKey in admin.site._registry
