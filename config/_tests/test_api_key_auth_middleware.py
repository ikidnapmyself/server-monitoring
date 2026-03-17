"""Tests for API key authentication middleware."""

import json

from django.test import Client, TestCase, override_settings

from config.models import APIKey


@override_settings(API_KEY_AUTH_ENABLED=True)
class APIKeyAuthMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_key = APIKey.objects.create(name="test-key")

    def test_post_without_key_returns_401(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"test": True}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_post_with_valid_bearer_key(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        # Should not be 401 — key is valid
        assert response.status_code != 401

    def test_post_with_valid_x_api_key(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_API_KEY=self.api_key.key,
        )
        assert response.status_code != 401

    def test_invalid_key_returns_401(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"test": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid-key-here",
        )
        assert response.status_code == 401

    def test_inactive_key_returns_401(self):
        self.api_key.is_active = False
        self.api_key.save()
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"test": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 401

    def test_admin_path_bypasses_auth(self):
        response = self.client.get("/admin/login/")
        assert response.status_code == 200

    def test_get_health_check_exempt(self):
        response = self.client.get("/alerts/webhook/")
        assert response.status_code == 200

    @override_settings(API_KEY_AUTH_ENABLED=False)
    def test_disabled_skips_auth(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
        )
        assert response.status_code != 401

    def test_last_used_at_updated(self):
        self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        self.api_key.refresh_from_db()
        assert self.api_key.last_used_at is not None

    def test_allowed_endpoints_restricts_access(self):
        self.api_key.allowed_endpoints = ["/alerts/"]
        self.api_key.save()
        # Allowed
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code != 403
        # Disallowed
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 403

    def test_non_api_path_bypasses_auth(self):
        # A path that's not in API_PATH_PREFIXES should pass through
        response = self.client.get("/nonexistent/")
        # 404, not 401
        assert response.status_code != 401
