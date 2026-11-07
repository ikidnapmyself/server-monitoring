"""Pipeline trigger endpoint."""

import json
import logging
import uuid

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineOrigin
from apps.orchestration.views.base import JSONResponseMixin

logger = logging.getLogger(__name__)


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

            # One transaction for the writes and the enqueue they justify: a crash
            # between them leaves an incident durably written with no run, and
            # nothing heals that (see the same note in ``apps.alerts.views.webhook``). With
            # ``sync`` the drain is registered with ``transaction.on_commit`` inside
            # ``enqueue_for``, so it runs after this block, never against rows the
            # transaction has not committed.
            with transaction.atomic():
                proc_result = AlertOrchestrator(trace_id=trace_id).process_webhook(
                    payload, driver=driver
                )
                runs = enqueue_for(
                    proc_result,
                    trace_id=trace_id,
                    origin=PipelineOrigin.INCOMING_WEBHOOK,
                    source=source,
                    environment=environment,
                    sync=(mode == "sync"),
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

        except Exception:  # noqa: BLE001 - the caller gets an honest 500
            # A fixed string, never ``str(e)``: the exception surface here is
            # driver detection plus the whole alert write, so its message can
            # carry database errors, settings paths and payload fragments. The
            # full traceback is already in the log above, keyed by trace_id,
            # which is where an operator looks; the body would only serve an
            # attacker. See the security standards in ``apps/alerts/AGENTS.md``.
            logger.exception("Unexpected error triggering pipeline (trace_id=%s)", trace_id)
            return self.error_response("Internal server error", status=500)

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
