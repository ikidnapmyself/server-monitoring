"""Memory analysis endpoint for the intelligence app."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


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
            recommendations = provider.run(analysis_type="memory")

            return self.json_response(
                {
                    "type": "memory",
                    "recommendations": [r.to_dict() for r in recommendations],
                    "count": len(recommendations),
                }
            )

        except Exception as e:
            return self.error_response(f"Error analyzing memory: {str(e)}", status=500)
