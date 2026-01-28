"""Providers list endpoint for the intelligence app."""

from django.views import View

from apps.intelligence.providers import list_providers
from apps.intelligence.views._mixins import JSONResponseMixin


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
