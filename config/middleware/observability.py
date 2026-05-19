"""ObservabilityMiddleware - bind trace_id/source per HTTP request.

Runs immediately after APIKeyAuthMiddleware so the API key (if any) is
already attached to request, and before the application views fire any
log calls.
"""

from __future__ import annotations

import uuid

from apps.observability import context


class ObservabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        trace_id = request.META.get("HTTP_X_TRACE_ID") or str(uuid.uuid4())
        token = context.bind(trace_id=trace_id, source="http")
        try:
            return self.get_response(request)
        finally:
            context.restore(token)
