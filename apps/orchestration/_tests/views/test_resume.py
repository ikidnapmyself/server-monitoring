import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings


@override_settings(API_KEY_AUTH_ENABLED=False)
class TestPipelineResumeView(TestCase):
    """Tests for PipelineResumeView (POST /orchestration/pipeline/<run_id>/resume/)."""

    def test_not_found(self):
        """Non-existent run_id returns 404."""
        client = Client()
        response = client.post("/orchestration/pipeline/no-such/resume/")

        assert response.status_code == 404

    def test_wrong_status(self):
        """Pipeline not in FAILED/RETRYING status returns 400."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-1", run_id="r-resume-bad", status=PipelineStatus.PENDING, source="test"
        )

        client = Client()
        response = client.post("/orchestration/pipeline/r-resume-bad/resume/")

        assert response.status_code == 400
        assert "cannot be resumed" in response.json()["error"]

    @patch("apps.orchestration.views.resume.PipelineOrchestrator")
    def test_valid_resume(self, mock_orch_cls):
        """Resuming a FAILED pipeline calls resume_pipeline and returns 200."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-1", run_id="r-resume-ok", status=PipelineStatus.FAILED, source="test"
        )

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"run_id": "r-resume-ok", "status": "completed"}
        mock_orch_cls.return_value.resume_pipeline.return_value = mock_result

        client = Client()
        response = client.post(
            "/orchestration/pipeline/r-resume-ok/resume/",
            data=json.dumps({"payload": {"x": 1}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        mock_orch_cls.return_value.resume_pipeline.assert_called_once()

    @patch("apps.orchestration.views.resume.PipelineOrchestrator")
    def test_a_resume_that_loses_the_run_is_a_conflict(self, mock_orch_cls):
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-1", run_id="r-resume-lost", status=PipelineStatus.FAILED, source="test"
        )
        mock_orch_cls.return_value.resume_pipeline.side_effect = ValueError(
            "Pipeline cannot be resumed from status: processing"
        )

        response = Client().post(
            "/orchestration/pipeline/r-resume-lost/resume/",
            data="{}",
            content_type="application/json",
        )

        assert response.status_code == 409
        assert response.json()["error"] == "Pipeline cannot be resumed from status: processing"

    def test_invalid_json(self):
        """Invalid JSON in resume request returns 400."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-1", run_id="r-resume-json", status=PipelineStatus.FAILED, source="test"
        )

        client = Client()
        response = client.post(
            "/orchestration/pipeline/r-resume-json/resume/",
            data=b"bad-json{{{",
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON body"
