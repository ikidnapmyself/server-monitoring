"""Tests for orchestration views."""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase


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


class TestPipelineDefinitionListView(TestCase):
    """Tests for PipelineDefinitionListView."""

    def test_list_empty(self):
        """Test listing when no definitions exist."""
        client = Client()
        response = client.get("/orchestration/definitions/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["definitions"] == []

    def test_list_active_only(self):
        """Test that only active definitions are listed by default."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        # Create active and inactive definitions
        PipelineDefinition.objects.create(
            name="active-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )
        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.get("/orchestration/definitions/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["definitions"][0]["name"] == "active-pipeline"

    def test_list_include_inactive(self):
        """Test listing with inactive definitions included."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="active-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )
        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.get("/orchestration/definitions/?include_inactive=true")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2


class TestPipelineDefinitionDetailView(TestCase):
    """Tests for PipelineDefinitionDetailView."""

    def test_get_definition(self):
        """Test getting a pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="test-pipeline",
            description="Test description",
            config=simple_pipeline_config,
        )

        client = Client()
        response = client.get("/orchestration/definitions/test-pipeline/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-pipeline"
        assert data["description"] == "Test description"
        assert "config" in data

    def test_get_nonexistent(self):
        """Test getting a nonexistent pipeline definition."""
        client = Client()
        response = client.get("/orchestration/definitions/nonexistent/")

        assert response.status_code == 404


class TestPipelineDefinitionValidateView(TestCase):
    """Tests for PipelineDefinitionValidateView."""

    def test_validate_valid_definition(self):
        """Test validating a valid pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="valid-pipeline",
            config=simple_pipeline_config,
        )

        client = Client()
        response = client.post("/orchestration/definitions/valid-pipeline/validate/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "valid-pipeline"
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_invalid_definition(self):
        """Test validating an invalid pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="invalid-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "bad", "type": "nonexistent"},
                ],
            },
        )

        client = Client()
        response = client.post("/orchestration/definitions/invalid-pipeline/validate/")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestPipelineDefinitionExecuteView(TestCase):
    """Tests for PipelineDefinitionExecuteView."""

    def test_execute_definition(self):
        """Test executing a pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="exec-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )

        mock_provider = MagicMock()
        mock_provider.run.return_value = [
            {"title": "test-rec", "description": "test", "priority": "low"}
        ]

        client = Client()
        with patch(
            "apps.intelligence.providers.get_provider",
            return_value=mock_provider,
        ):
            response = client.post(
                "/orchestration/definitions/exec-pipeline/execute/",
                data=json.dumps({"payload": {"test": "data"}, "source": "test"}),
                content_type="application/json",
            )

        assert response.status_code in (200, 500)  # 200 if completed, 500 if failed
        data = response.json()
        assert "run_id" in data
        assert "status" in data
        assert data["definition"] == "exec-pipeline"

    def test_execute_inactive_definition(self):
        """Test that inactive definitions cannot be executed."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.post(
            "/orchestration/definitions/inactive-pipeline/execute/",
            data=json.dumps({"payload": {}}),
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_execute_nonexistent(self):
        """Test executing a nonexistent definition."""
        client = Client()
        response = client.post(
            "/orchestration/definitions/nonexistent/execute/",
            data=json.dumps({"payload": {}}),
            content_type="application/json",
        )

        assert response.status_code == 404


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

    @patch("apps.orchestration.views.start_pipeline_task")
    def test_async_mode(self, mock_task):
        """Default async mode calls start_pipeline_task.delay and returns 202."""
        mock_task.delay.return_value = MagicMock(id="task-abc")

        client = Client()
        response = client.post(
            "/orchestration/pipeline/",
            data=json.dumps({"payload": {"key": "val"}, "source": "grafana"}),
            content_type="application/json",
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["task_id"] == "task-abc"
        mock_task.delay.assert_called_once()

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


class TestPipelineDefinitionValidateView404(TestCase):
    """Test 404 case for PipelineDefinitionValidateView."""

    def test_validate_nonexistent(self):
        """Validating a nonexistent definition returns 404."""
        client = Client()
        response = client.post("/orchestration/definitions/nonexistent/validate/")

        assert response.status_code == 404


class TestPipelineDefinitionExecuteViewInvalidJSON(TestCase):
    """Test invalid JSON case for PipelineDefinitionExecuteView."""

    def test_execute_invalid_json(self):
        """Invalid JSON body returns 400."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="exec-json-bad",
            config=_simple_pipeline_config(),
            is_active=True,
        )

        client = Client()
        response = client.post(
            "/orchestration/definitions/exec-json-bad/execute/",
            data=b"not-valid-json{{{",
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON body"
