"""Tests for Django system checks."""

from django.test import SimpleTestCase, override_settings


class RateLimitCacheCheckTests(SimpleTestCase):
    @override_settings(
        RATE_LIMIT_ENABLED=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_warns_on_locmem(self):
        from config.checks import check_rate_limit_cache

        errors = check_rate_limit_cache(None)
        assert len(errors) == 1
        assert errors[0].id == "config.W001"

    @override_settings(
        RATE_LIMIT_ENABLED=True,
        CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    )
    def test_warns_on_dummy(self):
        from config.checks import check_rate_limit_cache

        errors = check_rate_limit_cache(None)
        assert len(errors) == 1

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


class AuthDisabledCheckTests(SimpleTestCase):
    @override_settings(API_KEY_AUTH_ENABLED=False, DEBUG=False)
    def test_warns_when_auth_disabled_in_production(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 1
        assert errors[0].id == "config.W002"

    @override_settings(API_KEY_AUTH_ENABLED=False, DEBUG=True)
    def test_no_warning_in_debug_mode(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 0

    @override_settings(API_KEY_AUTH_ENABLED=True, DEBUG=False)
    def test_no_warning_when_auth_enabled(self):
        from config.checks import check_auth_enabled

        errors = check_auth_enabled(None)
        assert len(errors) == 0
