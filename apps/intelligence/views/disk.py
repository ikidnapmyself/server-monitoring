"""Disk analysis endpoint for the intelligence app."""

import logging

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.intelligence.providers import get_provider
from apps.intelligence.views._mixins import JSONResponseMixin
from config.security import PathNotAllowedError, resolve_safe_path

logger = logging.getLogger(__name__)


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
        raw_path = request.GET.get("path", "/")
        if raw_path == "/":
            path = "/"
        else:
            try:
                path = resolve_safe_path(raw_path)
            except PathNotAllowedError as e:
                logger.warning("Disk analysis path rejected: %s", e)
                return self.error_response("Invalid path", status=400)
        threshold_mb = float(request.GET.get("threshold_mb", 100))
        old_days = int(request.GET.get("old_days", 30))

        try:
            provider = get_provider(
                "local",
                large_file_threshold_mb=threshold_mb,
                old_file_days=old_days,
            )
            recommendations = provider.run(analysis_type="disk", path=path)

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
