"""
Views for the intelligence app.

Provides HTTP endpoints for intelligence recommendations.
"""

import json
from typing import Any

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import (
    get_provider,
    list_providers,
)


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data: Any, status: int = 200, safe: bool = True) -> JsonResponse:
        return JsonResponse(data, status=status, safe=safe)

    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)


@method_decorator(csrf_exempt, name="dispatch")
class RecommendationsView(JSONResponseMixin, View):
    """
    Get recommendations based on system state or a specific incident.

    GET /intelligence/recommendations/
        Returns recommendations based on current system state.

    GET /intelligence/recommendations/?incident_id=<id>
        Returns recommendations for a specific incident.

    POST /intelligence/recommendations/
        Accepts JSON body with optional incident_id and provider config.
    """

    def get(self, request):
        """Get recommendations, optionally for a specific incident."""
        incident_id = request.GET.get("incident_id")
        provider_name = request.GET.get("provider", "local")

        try:
            if incident_id:
                # Import here to avoid circular imports
                from apps.alerts.models import Incident

                try:
                    incident = Incident.objects.get(id=incident_id)
                    provider = get_provider(provider_name)
                    recommendations = provider.analyze(incident)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                provider = get_provider(provider_name)
                recommendations = provider.get_recommendations()

            return self.json_response(
                {
                    "provider": provider_name,
                    "incident_id": incident_id,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)

    def post(self, request):
        """Get recommendations with custom configuration."""
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        incident_id = body.get("incident_id")
        provider_name = body.get("provider", "local")
        provider_config = body.get("config", {})

        try:
            provider = get_provider(provider_name, **provider_config)

            if incident_id:
                from apps.alerts.models import Incident

                try:
                    incident = Incident.objects.get(id=incident_id)
                    recommendations = provider.analyze(incident)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                recommendations = provider.get_recommendations()

            return self.json_response(
                {
                    "provider": provider_name,
                    "incident_id": incident_id,
                    "config": provider_config,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)


@method_decorator(csrf_exempt, name="dispatch")
class MemoryAnalysisView(JSONResponseMixin, View):
    """
    Analyze memory usage and get recommendations.

    GET /intelligence/memory/
        Returns top memory-consuming processes and recommendations.
    """

    def get(self, request):
        """Get memory analysis and recommendations."""
        top_n = int(request.GET.get("top_n", 10))

        try:
            provider = get_provider("local", top_n_processes=top_n)
            recommendations = provider._get_memory_recommendations()

            return self.json_response(
                {
                    "type": "memory",
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except Exception as e:
            return self.error_response(f"Error analyzing memory: {str(e)}", status=500)


@method_decorator(csrf_exempt, name="dispatch")
class DiskAnalysisView(JSONResponseMixin, View):
    """
    Analyze disk usage and get recommendations.

    GET /intelligence/disk/
        Returns large files, old logs, and recommendations.

    GET /intelligence/disk/?path=/var/log&threshold_mb=50&old_days=7
        Customize the analysis parameters.
    """

    def get(self, request):
        """Get disk analysis and recommendations."""
        path = request.GET.get("path", "/")
        threshold_mb = float(request.GET.get("threshold_mb", 100))
        old_days = int(request.GET.get("old_days", 30))

        try:
            provider = get_provider(
                "local",
                large_file_threshold_mb=threshold_mb,
                old_file_days=old_days,
            )
            recommendations = provider._get_disk_recommendations(path)

            return self.json_response(
                {
                    "type": "disk",
                    "path": path,
                    "threshold_mb": threshold_mb,
                    "old_days": old_days,
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except Exception as e:
            return self.error_response(f"Error analyzing disk: {str(e)}", status=500)


class ProvidersListView(JSONResponseMixin, View):
    """
    List available intelligence providers.

    GET /intelligence/providers/
    """

    def get(self, request):
        """List all registered providers."""
        providers = list_providers()
        return self.json_response(
            {
                "providers": providers,
                "count": len(providers),
            }
        )


class HealthView(JSONResponseMixin, View):
    """
    Health check endpoint for the intelligence app.

    GET /intelligence/health/
    """

    def get(self, request):
        """Return health status."""
        return self.json_response(
            {
                "status": "healthy",
                "app": "intelligence",
                "providers": list_providers(),
            }
        )
