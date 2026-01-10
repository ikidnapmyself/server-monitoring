"""
Views for the orchestration app.

Provides HTTP endpoints for triggering and monitoring pipeline runs.
"""

import json
import logging
from typing import Any

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator
from apps.orchestration.tasks import start_pipeline_task

logger = logging.getLogger(__name__)


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data: Any, status: int = 200) -> JsonResponse:
        return JsonResponse(data, status=status)

    def error_response(self, message: str, status: int = 400) -> JsonResponse:
        return JsonResponse({"error": message}, status=status)


@method_decorator(csrf_exempt, name="dispatch")
class PipelineView(JSONResponseMixin, View):
    """
    API endpoint for triggering pipelines.

    POST /orchestration/pipeline/
        Start a new pipeline run.

    POST /orchestration/pipeline/sync/
        Start and wait for pipeline completion (synchronous).

    Request body:
    {
        "payload": {...},  // Alert payload to process
        "source": "grafana",  // Optional: source system
        "trace_id": "...",  // Optional: correlation ID
        "environment": "production"  // Optional: environment
    }
    """

    def post(self, request, mode: str = "async"):
        """Handle pipeline trigger request."""
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        payload = body.get("payload", {})
        source = body.get("source", "webhook")
        trace_id = body.get("trace_id")
        environment = body.get("environment", "production")

        if mode == "sync":
            # Synchronous execution
            orchestrator = PipelineOrchestrator()
            result = orchestrator.run_pipeline(
                payload={"payload": payload, **body},
                source=source,
                trace_id=trace_id,
                environment=environment,
            )
            return self.json_response(result.to_dict())
        else:
            # Async execution via Celery
            task_result = start_pipeline_task.delay(
                payload={"payload": payload, **body},
                source=source,
                trace_id=trace_id,
                environment=environment,
            )
            return self.json_response(
                {
                    "status": "queued",
                    "task_id": task_result.id,
                    "message": "Pipeline queued for execution",
                },
                status=202,
            )


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


@method_decorator(csrf_exempt, name="dispatch")
class PipelineResumeView(JSONResponseMixin, View):
    """
    API endpoint for resuming failed pipelines.

    POST /orchestration/pipeline/<run_id>/resume/
        Resume a failed pipeline run.
    """

    def post(self, request, run_id: str):
        """Resume a failed pipeline."""
        try:
            pipeline_run = PipelineRun.objects.get(run_id=run_id)
        except PipelineRun.DoesNotExist:
            return self.error_response(f"Pipeline run not found: {run_id}", status=404)

        if pipeline_run.status not in (PipelineStatus.FAILED, PipelineStatus.RETRYING):
            return self.error_response(
                f"Pipeline cannot be resumed from status: {pipeline_run.status}",
                status=400,
            )

        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        payload = body.get("payload", {})

        orchestrator = PipelineOrchestrator()
        result = orchestrator.resume_pipeline(
            run_id=run_id,
            payload={"payload": payload, **body},
        )

        return self.json_response(result.to_dict())
