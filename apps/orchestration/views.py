"""
Views for the orchestration app.

Provides HTTP endpoints for triggering and monitoring pipeline runs.
"""

import json
import logging
import uuid
from typing import Any

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator

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
        Ingest the payload, then leave one PENDING run per materially changed
        incident for the ``process_inbox`` drain. Returns 202.

    POST /orchestration/pipeline/sync/
        The same, but drain those runs before responding. Returns 200.

    Request body:
    {
        "payload": {...},  // Alert payload to process
        "driver": "grafana",  // Optional: force a driver instead of sniffing
        "source": "grafana",  // Optional: source system
        "trace_id": "...",  // Optional: correlation ID
        "environment": "production"  // Optional: environment
    }

    Response (async, 202):
        {"status": "accepted", "trace_id": ..., "incidents": [...]}
    Response (sync, 200):
        {"status": "completed", "trace_id": ..., "incidents": [...],
         "alerts": N, "errors": [...]}

    This is a producer like any other: producing an alert is not a pipeline
    stage, so the alert write happens here on the request thread and only the
    incident work (check, analyze, notify) becomes a run. ``mode`` decides who
    executes those runs, not which path they take.
    """

    def post(self, request, mode: str = "async"):
        """Handle pipeline trigger request."""
        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        payload = body.get("payload", {})
        source = body.get("source", "webhook")
        # A caller correlating its own push keeps its trace_id end to end; only
        # an anonymous call gets a fresh one.
        trace_id = body.get("trace_id") or str(uuid.uuid4())
        environment = body.get("environment", "production")
        # Preserved from the entry stage, which read ``driver`` off the wrapper it
        # was handed: naming a driver skips the sniff. Absent, the payload is
        # sniffed exactly as an unlabelled webhook is.
        driver = body.get("driver")

        try:
            from apps.alerts.services import AlertOrchestrator
            from apps.orchestration.intake import enqueue_for

            proc_result = AlertOrchestrator(trace_id=trace_id).process_webhook(
                payload, driver=driver
            )

            # Nothing understood the payload: no driver claimed it, so nothing was
            # parsed and nothing was written. A retry would fail identically, so
            # the caller is told to stop. Read off the flag the orchestrator sets
            # rather than matching its error text.
            if not proc_result.driver_resolved:
                logger.warning(
                    "No driver could handle the pipeline payload (source=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )
                return self.error_response("Could not detect a driver for the payload", status=400)

            # A driver understood it and we still wrote nothing: the fault is ours,
            # not the caller's. 400 would tell it to stop retrying and the push
            # would be silently discarded, so this is the one ingest failure that
            # must be a 5xx — there is no partial write for a retry to duplicate.
            if proc_result.errors and not proc_result.alerts:
                logger.error(
                    "Pipeline ingest wrote nothing (source=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )
                return self.error_response("Failed to process payload", status=500)

            # A payload that partially failed but wrote alerts is accepted: turning
            # it into a 5xx would have the caller retry and duplicate the work that
            # did land. The errors are logged, not swallowed.
            if proc_result.errors:
                logger.error(
                    "Pipeline ingest reported errors (source=%s, trace_id=%s): %s",
                    source,
                    trace_id,
                    "; ".join(proc_result.errors),
                )

            if not proc_result.alerts:
                # A misconfigured caller, not a failure: no rows, no run, no error.
                logger.warning(
                    "Pipeline payload carried no alerts (source=%s, trace_id=%s)",
                    source,
                    trace_id,
                )

            runs = enqueue_for(
                proc_result,
                trace_id=trace_id,
                origin=PipelineOrigin.INCOMING_WEBHOOK,
                source=source,
                environment=environment,
                sync=(mode == "sync"),
            )
        except Exception as e:  # noqa: BLE001 - the caller gets an honest 500
            logger.exception("Unexpected error triggering pipeline")
            return self.error_response(str(e), status=500)

        incident_ids = [run.incident_id for run in runs]
        logger.info(
            "Pipeline endpoint ingested %d alert(s), queued %d incident run(s) "
            "(mode=%s, source=%s, trace_id=%s)",
            len(proc_result.alerts),
            len(runs),
            mode,
            source,
            trace_id,
        )

        # ``run_id`` is gone from both shapes: one call now produces a run per
        # materially changed incident — several, or none — so there is no single
        # id to name. The trace_id plus the incident ids identify all of them, and
        # ``GET /orchestration/pipelines/?source=`` still lists the runs.
        if mode == "sync":
            # The runs were drained above, so this is a completed result. Only the
            # counts the ingest genuinely produced are reported; the same summary
            # ``manage.py run_pipeline`` prints for a replay.
            return self.json_response(
                {
                    "status": "completed",
                    "trace_id": trace_id,
                    "incidents": incident_ids,
                    "alerts": len(proc_result.alerts),
                    "errors": list(proc_result.errors),
                }
            )
        return self.json_response(
            {"status": "accepted", "trace_id": trace_id, "incidents": incident_ids},
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
