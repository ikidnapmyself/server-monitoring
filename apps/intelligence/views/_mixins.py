"""Shared mixins for intelligence views."""

from typing import Any

from django.http import JsonResponse


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data: Any, status: int = 200, safe: bool = True) -> JsonResponse:
        return JsonResponse(data, status=status, safe=safe)

    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)
