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
