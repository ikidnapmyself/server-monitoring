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
