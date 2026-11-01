"""Django system checks for config app."""

from django.conf import settings
from django.core import checks

from config.client_ip import invalid_entries


@checks.register()
def check_rate_limit_cache(app_configs, **kwargs):
    errors = []
    if not getattr(settings, "RATE_LIMIT_ENABLED", False):
        return errors

    cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "locmem" in cache_backend.lower() or "dummy" in cache_backend.lower():
        errors.append(
            checks.Warning(
                "Rate limiting is enabled with an in-memory cache backend. "
                "Rate limits will not be shared across processes. "
                "Use Redis or Memcached in production.",
                id="config.W001",
            )
        )
    return errors


@checks.register()
def check_auth_enabled(app_configs, **kwargs):
    errors = []
    if not getattr(settings, "API_KEY_AUTH_ENABLED", True):
        if not getattr(settings, "DEBUG", False):
            errors.append(
                checks.Warning(
                    "API key authentication is disabled (API_KEY_AUTH_ENABLED=False) "
                    "in a non-DEBUG environment. All API endpoints are unauthenticated. "
                    "Set API_KEY_AUTH_ENABLED=1 for production deployments.",
                    id="config.W002",
                )
            )
    return errors


@checks.register()
def check_trusted_proxies(app_configs, **kwargs):
    bad = invalid_entries(getattr(settings, "TRUSTED_PROXIES", ()))
    if not bad:
        return []
    return [
        checks.Error(
            f"TRUSTED_PROXIES has entries that aren't unix, an IP or a CIDR range: "
            f"{', '.join(bad)}.",
            id="config.E001",
        )
    ]
