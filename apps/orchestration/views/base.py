"""Shared mixins for orchestration views."""

from typing import Any

from django.http import JsonResponse


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data: Any, status: int = 200) -> JsonResponse:
        return JsonResponse(data, status=status)

    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)
