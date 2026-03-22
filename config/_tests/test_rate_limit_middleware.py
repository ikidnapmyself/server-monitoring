"""Tests for rate limiting middleware."""

import json
import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from config.models import APIKey


@override_settings(
    RATE_LIMIT_ENABLED=True,
    API_KEY_AUTH_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    RATE_LIMITS={"/alerts/": 5, "/notify/": 3},
)
@patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
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

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_uses_api_key_identity(self):
        key = APIKey.objects.create(name="client-a")
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
                HTTP_X_API_KEY=key._raw_key,
            )
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_API_KEY=key._raw_key,
        )
        assert response.status_code == 429

    def test_x_forwarded_for_does_not_bypass_rate_limit(self):
        """X-Forwarded-For is not trusted; different XFF values share the same REMOTE_ADDR bucket."""
        # Exhaust the limit using one XFF value
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR="1.2.3.4",
            )
        # A request with a *different* XFF value must still be rate-limited because
        # identity is based on REMOTE_ADDR (same for all test-client requests), not XFF.
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="9.9.9.9",
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

    @override_settings(
        RATE_LIMITS={"/alerts/": 10, "/alerts/webhook/": 2},
    )
    def test_longest_prefix_wins(self):
        """More-specific (longer) prefix limit applies over a shorter one."""
        cache.clear()
        # Exhaust the tighter /alerts/webhook/ limit (2 requests)
        for _ in range(2):
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

    def test_incr_value_error_falls_back_gracefully(self):
        """If cache.incr() raises ValueError (key evicted between add and incr),
        the middleware reseeds the key at 1 and lets the request through."""
        with patch("config.middleware.rate_limit.cache") as mock_cache:
            mock_cache.add.return_value = False  # key appears to exist
            mock_cache.incr.side_effect = ValueError("key not found")
            # second add() for the fallback seed
            mock_cache.add.side_effect = [False, True]
            response = self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
        # Request should pass through (count = 1, well under limit)
        assert response.status_code != 429
