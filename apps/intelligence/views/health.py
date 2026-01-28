"""Health check endpoint for the intelligence app."""

from django.views import View

from apps.intelligence.providers import list_providers
from apps.intelligence.views._mixins import JSONResponseMixin


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
