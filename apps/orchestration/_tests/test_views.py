"""Tests for orchestration views."""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings


def _simple_pipeline_config():
    """A simple pipeline configuration for testing."""
    return {
        "version": "1.0",
        "description": "Simple test pipeline",
        "defaults": {
            "max_retries": 3,
            "timeout_seconds": 300,
        },
        "nodes": [
            {
                "id": "analyze",
                "type": "intelligence",
                "config": {"provider": "local"},
                "next": "notify",
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {"driver": "generic"},
            },
        ],
    }


@override_settings(API_KEY_AUTH_ENABLED=False)
class TestPipelineView(TestCase):
    """Tests for PipelineView (POST /orchestration/pipeline/)."""

    def test_invalid_json(self):
        """Invalid JSON body returns 400."""
        client = Client()
        response = client.post(
            "/orchestration/pipeline/",
            data=b"not-json{{{",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON body"

    def test_async_mode_records_pending_run(self):
        """Default async mode records a PENDING run (broker-free) and returns 202."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        client = Client()
        response = client.post(
            "/orchestration/pipeline/",
            data=json.dumps({"payload": {"key": "val"}, "source": "grafana"}),
            content_type="application/json",
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        run = PipelineRun.objects.get(run_id=data["run_id"])
        assert run.status == PipelineStatus.PENDING

    @patch("apps.orchestration.views.PipelineOrchestrator")
    def test_sync_mode(self, mock_orch_cls):
        """Sync mode calls PipelineOrchestrator.run_pipeline and returns 200."""
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"run_id": "r-1", "status": "completed"}
        mock_orch_cls.return_value.run_pipeline.return_value = mock_result

        client = Client()
        response = client.post(
            "/orchestration/pipeline/sync/",
            data=json.dumps({"payload": {"x": 1}}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "r-1"
        mock_orch_cls.return_value.run_pipeline.assert_called_once()


@override_settings(API_KEY_AUTH_ENABLED=False)
class TestPipelineStatusView(TestCase):
    """Tests for PipelineStatusView (GET /orchestration/pipeline/<run_id>/)."""

    def test_pipeline_found(self):
        """Existing pipeline run returns its full status."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-100",
            run_id="r-100",
            status=PipelineStatus.PENDING,
            source="test",
        )

        client = Client()
        response = client.get("/orchestration/pipeline/r-100/")

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "r-100"
        assert data["trace_id"] == "t-100"
        assert data["status"] == "pending"
        assert "stage_executions" in data

    def test_pipeline_not_found(self):
        """Non-existent run_id returns 404."""
        client = Client()
        response = client.get("/orchestration/pipeline/no-such-id/")

        assert response.status_code == 404
        assert "not found" in response.json()["error"]


@override_settings(API_KEY_AUTH_ENABLED=False)
class TestPipelineListView(TestCase):
    """Tests for PipelineListView (GET /orchestration/pipelines/)."""

    def setUp(self):
        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t-1", run_id="r-1", status=PipelineStatus.PENDING, source="grafana"
        )
        PipelineRun.objects.create(
            trace_id="t-2", run_id="r-2", status=PipelineStatus.FAILED, source="alertmanager"
        )
        PipelineRun.objects.create(
            trace_id="t-3", run_id="r-3", status=PipelineStatus.PENDING, source="grafana"
        )

    def test_list_all(self):
        """List all pipeline runs."""
        client = Client()
        response = client.get("/orchestration/pipelines/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    def test_filter_by_status(self):
        """Filter pipeline runs by status."""
        client = Client()
        response = client.get("/orchestration/pipelines/?status=pending")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        for run in data["runs"]:
            assert run["status"] == "pending"

    def test_filter_by_source(self):
        """Filter pipeline runs by source."""
        client = Client()
        response = client.get("/orchestration/pipelines/?source=grafana")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        for run in data["runs"]:
            assert run["source"] == "grafana"


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

    @patch("apps.orchestration.views.PipelineOrchestrator")
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
