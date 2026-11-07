"""Pipeline run status endpoint."""

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineRun
from apps.orchestration.views.base import JSONResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class PipelineStatusView(JSONResponseMixin, View):
    """
    API endpoint for checking pipeline status.

    GET /orchestration/pipeline/<run_id>/
        Get status of a pipeline run.
    """

    def get(self, request, run_id: str):
        """Get pipeline run status."""
        try:
            pipeline_run = PipelineRun.objects.get(run_id=run_id)
        except PipelineRun.DoesNotExist:
            return self.error_response(f"Pipeline run not found: {run_id}", status=404)

        # Get stage executions
        stage_executions = list(
            pipeline_run.stage_executions.values(
                "stage",
                "status",
                "attempt",
                "started_at",
                "completed_at",
                "duration_ms",
                "error_type",
                "error_message",
            )
        )

        return self.json_response(
            {
                "trace_id": pipeline_run.trace_id,
                "run_id": pipeline_run.run_id,
                "status": pipeline_run.status,
                "current_stage": pipeline_run.current_stage,
                "incident_id": pipeline_run.incident_id,
                "source": pipeline_run.source,
                "environment": pipeline_run.environment,
                "total_attempts": pipeline_run.total_attempts,
                "intelligence_fallback_used": pipeline_run.intelligence_fallback_used,
                "created_at": pipeline_run.created_at.isoformat(),
                "started_at": (
                    pipeline_run.started_at.isoformat() if pipeline_run.started_at else None
                ),
                "completed_at": (
                    pipeline_run.completed_at.isoformat() if pipeline_run.completed_at else None
                ),
                "total_duration_ms": pipeline_run.total_duration_ms,
                "last_error": (
                    {
                        "type": pipeline_run.last_error_type,
                        "message": pipeline_run.last_error_message,
                        "retryable": pipeline_run.last_error_retryable,
                    }
                    if pipeline_run.last_error_type
                    else None
                ),
                "stage_executions": stage_executions,
            }
        )
