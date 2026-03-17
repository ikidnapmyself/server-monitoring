"""API key authentication middleware for stateless API access."""

import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger(__name__)

EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")
API_PATH_PREFIXES = ("/alerts/", "/orchestration/", "/notify/", "/intelligence/")

# Only these specific GET paths are treated as health checks and exempted from auth.
# Other GET endpoints that return operational data still require a valid API key.
HEALTH_CHECK_PATHS = (
    "/alerts/webhook/",
    "/intelligence/health/",
)


class APIKeyAuthMiddleware:
    """Stateless API key auth. Checks Bearer/X-API-Key header on API paths.

    Admin paths use Django session auth.
    Health-check GET paths are exempt; all other requests on API paths require a key.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "API_KEY_AUTH_ENABLED", True):
            return self.get_response(request)

        path = request.path

        if any(path.startswith(prefix) for prefix in EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        if not any(path.startswith(prefix) for prefix in API_PATH_PREFIXES):
            return self.get_response(request)

        if request.method == "GET" and any(path.startswith(p) for p in HEALTH_CHECK_PATHS):
            return self.get_response(request)

        key = self._extract_key(request)
        if not key:
            return JsonResponse(
                {
                    "error": (
                        "Authentication required. Provide API key via"
                        " Authorization: Bearer <key> or X-API-Key header."
                    )
                },
                status=401,
            )

        from config.models import APIKey

        try:
            api_key = APIKey.objects.get(key=key, is_active=True)
        except APIKey.DoesNotExist:
            return JsonResponse({"error": "Invalid or inactive API key."}, status=401)

        if api_key.allowed_endpoints:
            if not any(path.startswith(ep) for ep in api_key.allowed_endpoints):
                return JsonResponse(
                    {"error": "API key not authorized for this endpoint."},
                    status=403,
                )

        APIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        request.api_key = api_key

        return self.get_response(request)

    def _extract_key(self, request) -> str | None:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return request.META.get("HTTP_X_API_KEY")
