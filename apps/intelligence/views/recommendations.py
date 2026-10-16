"""Recommendations endpoint for the intelligence app."""

import json

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin
from config.failures import classify_failure


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

    A provider that fails while analysing gets a 502 whose ``failure`` is one of
    ``config.failures.FAILURE_KINDS``.
    """

    def get(self, request):
        """Get recommendations, optionally for a specific incident."""
        incident_id = request.GET.get("incident_id")
        provider_name = request.GET.get("provider", "local")

        try:
            run_kwargs = {}
            if incident_id:
                from apps.alerts.models import Incident

                try:
                    run_kwargs["incident"] = Incident.objects.get(id=incident_id)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            provider = get_provider(provider_name)
        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)

        return self._recommend(provider, provider_name, incident_id, run_kwargs)

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
            run_kwargs = {"provider_config": provider_config}

            if incident_id:
                from apps.alerts.models import Incident

                try:
                    run_kwargs["incident"] = Incident.objects.get(id=incident_id)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
        except KeyError as e:
            return self.error_response(str(e), status=400)
        except Exception as e:
            return self.error_response(f"Error generating recommendations: {str(e)}", status=500)

        return self._recommend(provider, provider_name, incident_id, run_kwargs)

    def _recommend(self, provider, provider_name, incident_id, run_kwargs):
        try:
            recommendations = provider.run(**run_kwargs)
        except Exception as e:
            kind = classify_failure(e)
            return self.json_response(
                {"error": f"Provider {provider_name} failed ({kind}): {e}", "failure": kind},
                status=502,
            )

        return self.json_response(
            {
                "provider": provider_name,
                "incident_id": incident_id,
                "recommendations": [r.to_dict() for r in recommendations],
                "count": len(recommendations),
            }
        )
