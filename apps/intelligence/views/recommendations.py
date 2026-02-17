"""Recommendations endpoint for the intelligence app."""

import json

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


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
                from apps.alerts.models import Incident

                try:
                    incident = Incident.objects.get(id=incident_id)
                    provider = get_provider(provider_name)
                    recommendations = provider.run(incident=incident)
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                provider = get_provider(provider_name)
                recommendations = provider.run()

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
                    recommendations = provider.run(
                        incident=incident, provider_config=provider_config
                    )
                except Incident.DoesNotExist:
                    return self.error_response(
                        f"Incident with id {incident_id} not found", status=404
                    )
            else:
                recommendations = provider.run(provider_config=provider_config)

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
