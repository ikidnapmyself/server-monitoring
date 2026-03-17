---
title: "Security Hardening Implementation Plan"
parent: Plans
---

# Security Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add API key authentication, webhook signature verification, and rate limiting for internet-exposed deployment.

**Architecture:** Three layers — (1) `APIKey` model + auth middleware for stateless API key auth, (2) signature verification built into `BaseAlertDriver` so each driver self-describes its HMAC header, (3) rate limiting middleware using Django cache with sliding window counters.

**Tech Stack:** Django middleware, Django cache framework, HMAC (stdlib), Django system checks.

---

### Task 1: APIKey model

**Files:**
- Create: `config/models.py`
- Create: `config/migrations/0001_initial.py` (via makemigrations)
- Test: `config/_tests/test_api_key_model.py`

**Step 1: Write the failing test**

```python
"""Tests for the APIKey model."""

from django.test import TestCase

from config.models import APIKey


class APIKeyModelTests(TestCase):
    def test_create_api_key(self):
        key = APIKey.objects.create(name="test-client")
        assert key.key  # auto-generated
        assert len(key.key) == 40
        assert key.is_active is True
        assert key.name == "test-client"

    def test_key_is_unique(self):
        k1 = APIKey.objects.create(name="a")
        k2 = APIKey.objects.create(name="b")
        assert k1.key != k2.key

    def test_str_representation(self):
        key = APIKey.objects.create(name="my-service")
        assert "my-service" in str(key)

    def test_generate_key_class_method(self):
        raw = APIKey.generate_key()
        assert len(raw) == 40
        assert isinstance(raw, str)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/test_api_key_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'APIKey' from 'config.models'`

**Step 3: Write minimal implementation**

Create `config/models.py`:

```python
"""Models for the config app (API keys, etc.)."""

import secrets

from django.db import models


class APIKey(models.Model):
    """API key for authenticating stateless API requests."""

    key = models.CharField(max_length=40, unique=True, db_index=True, editable=False)
    name = models.CharField(max_length=100, help_text="Human-readable label for this key")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    allowed_endpoints = models.JSONField(
        default=list,
        blank=True,
        help_text="Optional list of path prefixes this key can access. Empty = all.",
    )

    class Meta:
        db_table = "config_api_key"
        verbose_name = "API Key"
        verbose_name_plural = "API Keys"

    def __str__(self) -> str:
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_key() -> str:
        return secrets.token_hex(20)
```

**Step 4: Run makemigrations and test**

Run: `uv run python manage.py makemigrations config`
Then: `uv run pytest config/_tests/test_api_key_model.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add config/models.py config/migrations/ config/_tests/test_api_key_model.py
git commit -m "feat: add APIKey model for stateless API authentication"
```

---

### Task 2: APIKey admin registration

**Files:**
- Modify: `config/admin.py`
- Test: `config/_tests/test_api_key_admin.py`

**Step 1: Write the failing test**

```python
"""Tests for APIKey admin registration."""

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory

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
        # Should show first 8 chars and mask the rest
        assert key.key[:8] in masked
        assert "***" in masked
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/test_api_key_admin.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `config/admin.py`:

```python
from django.contrib import admin

from config.models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    readonly_fields = ["key", "created_at", "last_used_at"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.key[:8]}***"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest config/_tests/test_api_key_admin.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add config/admin.py config/_tests/test_api_key_admin.py
git commit -m "feat: register APIKey in admin with masked key display"
```

---

### Task 3: API key authentication middleware

**Files:**
- Create: `config/middleware/__init__.py`
- Create: `config/middleware/api_key_auth.py`
- Test: `config/_tests/test_api_key_auth_middleware.py`

**Step 1: Write the failing tests**

```python
"""Tests for API key authentication middleware."""

import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from config.models import APIKey


class APIKeyAuthMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_key = APIKey.objects.create(name="test-key")

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_api_path_without_key_returns_401(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"test": True}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_api_path_with_valid_bearer_key(self):
        response = self.client.get(
            "/alerts/webhook/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 200

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_api_path_with_valid_x_api_key(self):
        response = self.client.get(
            "/alerts/webhook/",
            HTTP_X_API_KEY=self.api_key.key,
        )
        assert response.status_code == 200

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_invalid_key_returns_401(self):
        response = self.client.get(
            "/alerts/webhook/",
            HTTP_AUTHORIZATION="Bearer invalid-key-here",
        )
        assert response.status_code == 401

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_inactive_key_returns_401(self):
        self.api_key.is_active = False
        self.api_key.save()
        response = self.client.get(
            "/alerts/webhook/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 401

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_admin_path_bypasses_auth(self):
        # Admin paths should not require API key
        response = self.client.get("/admin/login/")
        assert response.status_code == 200

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_get_health_check_exempt(self):
        response = self.client.get("/alerts/webhook/")
        assert response.status_code == 200

    @override_settings(API_KEY_AUTH_ENABLED=False)
    def test_disabled_skips_auth(self):
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"test": True}),
            content_type="application/json",
        )
        # Should not be 401 — middleware is disabled
        assert response.status_code != 401

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_last_used_at_updated(self):
        self.client.get(
            "/alerts/webhook/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        self.api_key.refresh_from_db()
        assert self.api_key.last_used_at is not None

    @override_settings(API_KEY_AUTH_ENABLED=True)
    def test_allowed_endpoints_restricts_access(self):
        self.api_key.allowed_endpoints = ["/alerts/"]
        self.api_key.save()
        # Allowed path
        response = self.client.get(
            "/alerts/webhook/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 200
        # Disallowed path
        response = self.client.get(
            "/notify/send/",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key.key}",
        )
        assert response.status_code == 403
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/test_api_key_auth_middleware.py -v`
Expected: FAIL — middleware doesn't exist yet

**Step 3: Write minimal implementation**

Create `config/middleware/__init__.py` (empty).

Create `config/middleware/api_key_auth.py`:

```python
"""API key authentication middleware for stateless API access."""

import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

# Paths that bypass API key auth entirely
EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")

# API path prefixes that require authentication
API_PATH_PREFIXES = ("/alerts/", "/orchestration/", "/notify/", "/intelligence/")


class APIKeyAuthMiddleware:
    """Middleware that enforces API key authentication on API endpoints.

    Stateless — checks Authorization: Bearer <key> or X-API-Key header.
    Admin paths use Django session auth (untouched).
    GET requests to API paths are exempt (health checks).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "API_KEY_AUTH_ENABLED", True):
            return self.get_response(request)

        path = request.path

        # Skip exempt paths (admin, static)
        if any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        # Only enforce on API paths
        if not any(path.startswith(prefix) for prefix in API_PATH_PREFIXES):
            return self.get_response(request)

        # GET requests are exempt (health checks)
        if request.method == "GET":
            return self.get_response(request)

        # Extract API key from header
        key = self._extract_key(request)
        if not key:
            return JsonResponse(
                {"error": "Authentication required. Provide API key via Authorization: Bearer <key> or X-API-Key header."},
                status=401,
            )

        # Validate key
        from config.models import APIKey

        try:
            api_key = APIKey.objects.get(key=key, is_active=True)
        except APIKey.DoesNotExist:
            return JsonResponse({"error": "Invalid or inactive API key."}, status=401)

        # Check endpoint restrictions
        if api_key.allowed_endpoints:
            if not any(path.startswith(ep) for ep in api_key.allowed_endpoints):
                return JsonResponse(
                    {"error": "API key not authorized for this endpoint."},
                    status=403,
                )

        # Update last_used_at (non-blocking, best-effort)
        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())

        # Attach key to request for downstream use
        request.api_key = api_key

        return self.get_response(request)

    def _extract_key(self, request) -> str | None:
        # Try Authorization: Bearer <key>
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()

        # Try X-API-Key header
        return request.META.get("HTTP_X_API_KEY")
```

**Step 4: Add middleware to settings**

In `config/settings.py`, add after `XFrameOptionsMiddleware`:

```python
    "config.middleware.api_key_auth.APIKeyAuthMiddleware",
```

And add settings:

```python
# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
API_KEY_AUTH_ENABLED = os.environ.get("API_KEY_AUTH_ENABLED", "0") == "1"
```

**Step 5: Run tests**

Run: `uv run pytest config/_tests/test_api_key_auth_middleware.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add config/middleware/ config/_tests/test_api_key_auth_middleware.py config/settings.py
git commit -m "feat: add API key authentication middleware"
```

---

### Task 4: Webhook signature verification on BaseAlertDriver

**Files:**
- Modify: `apps/alerts/drivers/base.py`
- Modify: `apps/alerts/drivers/grafana.py`
- Modify: `apps/alerts/drivers/pagerduty.py`
- Modify: `apps/alerts/drivers/newrelic.py`
- Modify: `apps/alerts/drivers/generic.py`
- Modify: `apps/alerts/views.py`
- Test: `apps/alerts/_tests/test_signature_verification.py`

**Step 1: Write the failing tests**

```python
"""Tests for webhook signature verification."""

import hashlib
import hmac
import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from apps.alerts.drivers.base import BaseAlertDriver
from apps.alerts.drivers.grafana import GrafanaDriver
from apps.alerts.drivers.generic import GenericWebhookDriver


class BaseDriverSignatureTests(TestCase):
    def test_base_driver_has_no_signature_header(self):
        # BaseAlertDriver defaults
        assert BaseAlertDriver.signature_header is None
        assert BaseAlertDriver.signature_algorithm == "sha256"

    def test_verify_signature_valid(self):
        driver = GenericWebhookDriver()
        body = b'{"test": true}'
        secret = "my-secret"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert driver.verify_signature(body, expected, secret) is True

    def test_verify_signature_invalid(self):
        driver = GenericWebhookDriver()
        assert driver.verify_signature(b"body", "wrong-sig", "secret") is False

    def test_verify_signature_sha256_prefix(self):
        driver = GenericWebhookDriver()
        body = b'{"test": true}'
        secret = "my-secret"
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # Some providers send "sha256=<hex>"
        assert driver.verify_signature(body, f"sha256={digest}", secret) is True


class GrafanaDriverSignatureTests(TestCase):
    def test_grafana_has_signature_header(self):
        assert GrafanaDriver.signature_header == "X-Grafana-Signature"


class WebhookSignatureIntegrationTests(TestCase):
    @patch.dict(os.environ, {"WEBHOOK_SECRET_GENERIC": "test-secret", "ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_valid_signature_accepted(self):
        payload = json.dumps({"name": "Test", "status": "firing"})
        body = payload.encode()
        sig = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=sig,
        )
        assert response.status_code != 403

    @patch.dict(os.environ, {"WEBHOOK_SECRET_GENERIC": "test-secret", "ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_invalid_signature_rejected(self):
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE="invalid-signature",
        )
        assert response.status_code == 403

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    def test_no_secret_configured_skips_verification(self):
        payload = json.dumps({"name": "Test", "status": "firing"})

        client = Client()
        response = client.post(
            "/alerts/webhook/generic/",
            data=payload,
            content_type="application/json",
        )
        # Should not be 403 — no secret configured, verification skipped
        assert response.status_code != 403
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_signature_verification.py -v`
Expected: FAIL — `signature_header` not on BaseAlertDriver

**Step 3: Write minimal implementation**

Add to `apps/alerts/drivers/base.py` on `BaseAlertDriver`:

```python
    signature_header: str | None = None
    signature_algorithm: str = "sha256"

    def verify_signature(self, request_body: bytes, header_value: str, secret: str) -> bool:
        """Verify HMAC signature. Override for non-standard schemes."""
        import hashlib
        import hmac as hmac_mod

        expected = hmac_mod.new(secret.encode(), request_body, hashlib.sha256).hexdigest()

        # Handle "sha256=<hex>" prefix format
        clean_header = header_value
        if "=" in header_value and not header_value.startswith("sha"):
            pass  # use as-is
        elif header_value.startswith(("sha256=", "sha1=")):
            clean_header = header_value.split("=", 1)[1]

        return hmac_mod.compare_digest(expected, clean_header)
```

Set `signature_header` on drivers:

```python
# grafana.py
class GrafanaDriver(BaseAlertDriver):
    signature_header = "X-Grafana-Signature"

# pagerduty.py
class PagerDutyDriver(BaseAlertDriver):
    signature_header = "X-PagerDuty-Signature"

# newrelic.py
class NewRelicDriver(BaseAlertDriver):
    signature_header = "X-NewRelic-Signature"

# generic.py
class GenericWebhookDriver(BaseAlertDriver):
    signature_header = "X-Webhook-Signature"
```

Add signature check to `apps/alerts/views.py` in `AlertWebhookView.post()`, after JSON parsing and before Celery dispatch:

```python
            # Verify webhook signature if configured
            from apps.alerts.drivers import detect_driver, get_driver

            driver_instance = None
            if driver:
                try:
                    driver_instance = get_driver(driver)
                except ValueError:
                    pass
            else:
                driver_instance = detect_driver(payload)

            if driver_instance and driver_instance.signature_header:
                secret_env = f"WEBHOOK_SECRET_{driver_instance.name.upper()}"
                secret = os.environ.get(secret_env)
                if secret:
                    sig_header = request.META.get(
                        f"HTTP_{driver_instance.signature_header.upper().replace('-', '_')}"
                    )
                    if not sig_header or not driver_instance.verify_signature(
                        request.body, sig_header, secret
                    ):
                        return JsonResponse(
                            {"status": "error", "message": "Invalid webhook signature"},
                            status=403,
                        )
```

**Step 4: Run tests**

Run: `uv run pytest apps/alerts/_tests/test_signature_verification.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/drivers/ apps/alerts/views.py apps/alerts/_tests/test_signature_verification.py
git commit -m "feat: add webhook signature verification to alert drivers"
```

---

### Task 5: Rate limiting middleware

**Files:**
- Create: `config/middleware/rate_limit.py`
- Test: `config/_tests/test_rate_limit_middleware.py`

**Step 1: Write the failing tests**

```python
"""Tests for rate limiting middleware."""

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from config.models import APIKey


@override_settings(
    RATE_LIMIT_ENABLED=True,
    API_KEY_AUTH_ENABLED=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    RATE_LIMITS={
        "/alerts/": 5,
        "/notify/": 3,
    },
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
        data = response.json()
        assert "Rate limit exceeded" in data["error"]
        assert "Retry-After" in response

    def test_different_paths_separate_limits(self):
        # Fill alerts limit
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
            )
        # Notify should still work
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

    def test_rate_limit_uses_api_key_when_available(self):
        key = APIKey.objects.create(name="client-a")
        for _ in range(5):
            self.client.post(
                "/alerts/webhook/",
                data=json.dumps({"name": "Test", "status": "firing"}),
                content_type="application/json",
                HTTP_X_API_KEY=key.key,
            )
        # Same key should be limited
        response = self.client.post(
            "/alerts/webhook/",
            data=json.dumps({"name": "Test", "status": "firing"}),
            content_type="application/json",
            HTTP_X_API_KEY=key.key,
        )
        assert response.status_code == 429
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/test_rate_limit_middleware.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Create `config/middleware/rate_limit.py`:

```python
"""Rate limiting middleware using Django cache."""

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)

EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")

# Default rate limits (requests per minute) by path prefix
DEFAULT_RATE_LIMITS = {
    "/alerts/": 120,
    "/orchestration/": 30,
    "/notify/": 30,
    "/intelligence/": 20,
}


class RateLimitMiddleware:
    """Sliding window rate limiter using Django cache.

    Limits are per API key (if present) or per IP, per path prefix, per minute.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "RATE_LIMIT_ENABLED", False):
            return self.get_response(request)

        path = request.path

        # Exempt paths
        if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        # GET requests exempt (health checks)
        if request.method == "GET":
            return self.get_response(request)

        # Find matching rate limit
        rate_limits = getattr(settings, "RATE_LIMITS", DEFAULT_RATE_LIMITS)
        limit = None
        matched_prefix = None
        for prefix, max_requests in rate_limits.items():
            if path.startswith(prefix):
                limit = max_requests
                matched_prefix = prefix
                break

        if limit is None:
            return self.get_response(request)

        # Build cache key
        identity = self._get_identity(request)
        window = int(time.time() // 60)  # per-minute window
        cache_key = f"ratelimit:{identity}:{matched_prefix}:{window}"

        # Increment counter
        count = cache.get(cache_key, 0)
        if count >= limit:
            retry_after = 60 - int(time.time() % 60)
            response = JsonResponse(
                {"error": "Rate limit exceeded. Try again later."},
                status=429,
            )
            response["Retry-After"] = str(retry_after)
            return response

        cache.set(cache_key, count + 1, timeout=120)  # 2 min TTL

        return self.get_response(request)

    def _get_identity(self, request) -> str:
        # Prefer API key name if available
        api_key = getattr(request, "api_key", None)
        if api_key:
            return f"key:{api_key.name}"

        # Fall back to IP
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return f"ip:{xff.split(',')[0].strip()}"
        return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"
```

**Step 4: Add middleware to settings**

In `config/settings.py`, add after `APIKeyAuthMiddleware`:

```python
    "config.middleware.rate_limit.RateLimitMiddleware",
```

And add settings:

```python
# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "0") == "1"

RATE_LIMITS = {
    "/alerts/": 120,       # 120 req/min
    "/orchestration/": 30, # 30 req/min
    "/notify/": 30,        # 30 req/min
    "/intelligence/": 20,  # 20 req/min
}
```

**Step 5: Run tests**

Run: `uv run pytest config/_tests/test_rate_limit_middleware.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add config/middleware/rate_limit.py config/_tests/test_rate_limit_middleware.py config/settings.py
git commit -m "feat: add rate limiting middleware with sliding window counters"
```

---

### Task 6: Django system check for cache backend

**Files:**
- Create: `config/checks.py`
- Test: `config/_tests/test_checks.py`

**Step 1: Write the failing test**

```python
"""Tests for Django system checks."""

from django.test import SimpleTestCase, override_settings


class RateLimitCacheCheckTests(SimpleTestCase):
    @override_settings(
        RATE_LIMIT_ENABLED=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_warns_on_locmem_with_rate_limiting(self):
        from config.checks import check_rate_limit_cache

        errors = check_rate_limit_cache(None)
        assert len(errors) == 1
        assert errors[0].id == "config.W001"

    @override_settings(
        RATE_LIMIT_ENABLED=False,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_no_warning_when_disabled(self):
        from config.checks import check_rate_limit_cache

        errors = check_rate_limit_cache(None)
        assert len(errors) == 0

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache"}},
    )
    def test_no_warning_with_redis(self):
        from config.checks import check_rate_limit_cache

        errors = check_rate_limit_cache(None)
        assert len(errors) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest config/_tests/test_checks.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Create `config/checks.py`:

```python
"""Django system checks for config app."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_rate_limit_cache(app_configs, **kwargs):
    errors = []
    if not getattr(settings, "RATE_LIMIT_ENABLED", False):
        return errors

    cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "locmem" in cache_backend.lower() or "dummy" in cache_backend.lower():
        errors.append(
            Warning(
                "Rate limiting is enabled with an in-memory cache backend. "
                "Rate limits will not be shared across processes. "
                "Use Redis or Memcached in production.",
                id="config.W001",
            )
        )
    return errors
```

**Step 4: Run tests**

Run: `uv run pytest config/_tests/test_checks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add config/checks.py config/_tests/test_checks.py
git commit -m "feat: add Django system check for rate limit cache backend"
```

---

### Task 7: Update Security.md and final verification

**Files:**
- Modify: `docs/Security.md`

**Step 1: Update Security.md**

Add sections documenting:
- API Key Authentication (how to create keys, header format, endpoint restrictions)
- Webhook Signature Verification (env vars, per-driver support table)
- Rate Limiting (default limits, configuration, cache requirements)

**Step 2: Run full test suite**

```bash
uv run pytest
```

Expected: All tests pass.

**Step 3: Run coverage**

```bash
uv run coverage run -m pytest && uv run coverage report --include="config/middleware/*.py,config/models.py,config/checks.py,apps/alerts/drivers/base.py" --show-missing
```

Expected: 100% on new code.

**Step 4: Run pre-commit hooks**

```bash
uv run pre-commit run --all-files
```

**Step 5: Commit**

```bash
git add docs/Security.md
git commit -m "docs: update Security.md with hardening features"
```