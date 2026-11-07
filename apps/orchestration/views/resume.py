"""Failed pipeline resume endpoint."""

import json

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.models import PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator
from apps.orchestration.views.base import JSONResponseMixin


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
        try:
            result = orchestrator.resume_pipeline(
                run_id=run_id,
                payload={"payload": payload, **body},
            )
        except ValueError as exc:
            return self.error_response(str(exc), status=409)

        return self.json_response(result.to_dict())
