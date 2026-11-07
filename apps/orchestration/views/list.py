"""Pipeline run listing endpoint."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineRun
from apps.orchestration.views.base import JSONResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class PipelineListView(JSONResponseMixin, View):
    """
    API endpoint for listing pipeline runs.

    GET /orchestration/pipelines/
        List recent pipeline runs.

    Query params:
        status: Filter by status (pending, ingested, checked, analyzed, notified, failed, retrying)
        source: Filter by source
        limit: Max results (default 50)
    """

    def get(self, request):
        """List pipeline runs."""
        status = request.GET.get("status")
        source = request.GET.get("source")
        limit = int(request.GET.get("limit", 50))

        queryset = PipelineRun.objects.all()

        if status:
            queryset = queryset.filter(status=status)
        if source:
            queryset = queryset.filter(source=source)

        queryset = queryset.order_by("-created_at")[:limit]

        runs = [
            {
                "trace_id": run.trace_id,
                "run_id": run.run_id,
                "status": run.status,
                "current_stage": run.current_stage,
                "source": run.source,
                "created_at": run.created_at.isoformat(),
                "total_duration_ms": run.total_duration_ms,
            }
            for run in queryset
        ]

        return self.json_response({"count": len(runs), "runs": runs})
