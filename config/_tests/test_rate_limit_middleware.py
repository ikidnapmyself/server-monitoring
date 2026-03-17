"""Tests for rate limiting middleware."""

import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from config.models import APIKey


@override_settings(
    RATE_LIMIT_ENABLED=True,
    API_KEY_AUTH_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    RATE_LIMITS={"/alerts/": 5, "/notify/": 3},
)
class RateLimitMiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_under_limit_succeeds(self):
        for _ in range(5):
            response = self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
            assert response.status_code != 429

    def test_over_limit_returns_429(self):
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
        )
        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["error"]
        assert "Retry-After" in response

    def test_different_paths_separate_limits(self):
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        assert response.status_code != 429

    def test_get_requests_exempt(self):
        for _ in range(10):
            response = self.client.get("/alerts/webhook/")
            assert response.status_code != 429

    def test_admin_paths_exempt(self):
        for _ in range(10):
            response = self.client.get("/admin/login/")
            assert response.status_code != 429

    @override_settings(RATE_LIMIT_ENABLED=False)
    def test_disabled_skips_limiting(self):
        for _ in range(20):
            response = self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
        assert response.status_code != 429

    def test_uses_api_key_identity(self):
        key = APIKey.objects.create(name="client-a")
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
                HTTP_X_API_KEY=key.key,
            )
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_API_KEY=key.key,
        )
        assert response.status_code == 429

    def test_x_forwarded_for_identity(self):
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR="1.2.3.4",
            )
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="1.2.3.4",
        )
        assert response.status_code == 429

    def test_unmatched_path_not_limited(self):
        for _ in range(20):
            response = self.client.post(
                "/nonexistent/",
                data=json.dumps({}),
                content_type="application/json",
            )
        # Should be 404 not 429
        assert response.status_code != 429
