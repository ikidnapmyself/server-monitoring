"""Rate limiting middleware using Django cache."""

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)

EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")

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

        if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        if request.method == "GET":
            return self.get_response(request)

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

        identity = self._get_identity(request)
        window = int(time.time() // 60)
        cache_key = f"ratelimit:{identity}:{matched_prefix}:{window}"

        count = cache.get(cache_key, 0)
        if count >= limit:
            retry_after = 60 - int(time.time() % 60)
            response = JsonResponse(
                {"error": "Rate limit exceeded. Try again later."},
                status=429,
            )
            response["Retry-After"] = str(retry_after)
            return response

        cache.set(cache_key, count + 1, timeout=120)

        return self.get_response(request)

    def _get_identity(self, request) -> str:
        api_key = getattr(request, "api_key", None)
        if api_key:
            return f"key:{api_key.name}"

        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return f"ip:{xff.split(',')[0].strip()}"
        return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"
