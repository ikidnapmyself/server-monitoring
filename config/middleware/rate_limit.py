"""Rate limiting middleware using Django cache."""

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from config.middleware.constants import EXEMPT_PATH_PREFIXES

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMITS = {
    "/alerts/": 120,
    "/orchestration/": 30,
    "/notify/": 30,
    "/intelligence/": 20,
}


class RateLimitMiddleware:
    """Fixed-window rate limiter using Django cache.

    Limits are per API key (if present) or per IP, per path prefix, per minute.
    The window resets at the start of each UTC minute.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "RATE_LIMIT_ENABLED", False):
            return self.get_response(request)

        path = request.path

        if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        if request.method == "GET":
            return self.get_response(request)

        rate_limits = getattr(settings, "RATE_LIMITS", DEFAULT_RATE_LIMITS)
        limit = None
        matched_prefix = None
        # Sort by length descending so the most-specific (longest) prefix wins.
        for prefix in sorted(rate_limits, key=len, reverse=True):
            if path.startswith(prefix):
                limit = rate_limits[prefix]
                matched_prefix = prefix
                break

        if limit is None:
            return self.get_response(request)

        identity = self._get_identity(request)
        window = int(time.time() // 60)
        cache_key = f"ratelimit:{identity}:{matched_prefix}:{window}"

        # Atomic increment: add initializes the key to 1 (returns True) when it
        # doesn't exist yet; incr bumps it when it already exists.  Both
        # operations are atomic on all cache backends that support them, avoiding
        # the non-atomic get-then-set race that could undercount under load.
        # If the key expires in the tiny window between add() returning False and
        # the incr() call, incr() raises ValueError on some backends — we handle
        # that by re-seeding the key at 1 (conservative: treats it as a fresh window).
        if not cache.add(cache_key, 1, timeout=120):
            try:
                count = cache.incr(cache_key)
            except ValueError:
                cache.add(cache_key, 1, timeout=120)
                count = 1
        else:
            count = 1

        # count is already post-increment, so `> limit` allows exactly `limit`
        # requests per window (count 1…limit pass; count limit+1 is rejected).
        if count > limit:
            retry_after = 60 - int(time.time() % 60)
            response = JsonResponse(
                {"error": "Rate limit exceeded. Try again later."},
                status=429,
            )
            response["Retry-After"] = str(retry_after)
            return response

        return self.get_response(request)

    def _get_identity(self, request) -> str:
        api_key = getattr(request, "api_key", None)
        if api_key:
            parts = ("key", api_key.name)
        else:
            parts = ("ip", request.META.get("REMOTE_ADDR", "unknown"))
        return ":".join(parts)
