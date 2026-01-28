"""Disk analysis endpoint for the intelligence app."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin


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
