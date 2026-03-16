"""Tests for orchestration Celery tasks."""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class RunPipelineTaskTests(TestCase):
    """Tests for run_pipeline_task."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator")
    def test_run_pipeline_task(self, mock_orchestrator_cls):
        from apps.orchestration.tasks import run_pipeline_task

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator_cls.return_value.run_pipeline.return_value = mock_result

        async_result = run_pipeline_task.apply(
            kwargs={
                "payload": {"name": "test"},
                "source": "grafana",
                "trace_id": "t-123",
                "environment": "staging",
            }
        )
        result = async_result.get()

        assert result == {"status": "COMPLETED"}
        mock_orchestrator_cls.return_value.run_pipeline.assert_called_once_with(
            payload={"name": "test"},
            source="grafana",
            trace_id="t-123",
            environment="staging",
        )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class ResumePipelineTaskTests(TestCase):
    """Tests for resume_pipeline_task."""

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator")
    def test_resume_pipeline_task(self, mock_orchestrator_cls):
        from apps.orchestration.tasks import resume_pipeline_task

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"status": "COMPLETED"}
        mock_orchestrator_cls.return_value.resume_pipeline.return_value = mock_result

        async_result = resume_pipeline_task.apply(
            kwargs={
                "run_id": "run-456",
                "payload": {"name": "test"},
            }
        )
        result = async_result.get()

        assert result == {"status": "COMPLETED"}
        mock_orchestrator_cls.return_value.resume_pipeline.assert_called_once_with(
            run_id="run-456",
            payload={"name": "test"},
        )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class StartPipelineTaskTests(TestCase):
    """Tests for start_pipeline_task."""

    @patch("apps.orchestration.tasks.run_pipeline_task")
    @patch("apps.orchestration.orchestrator.PipelineOrchestrator")
    def test_start_pipeline_task(self, mock_orchestrator_cls, mock_run_task):
        from apps.orchestration.tasks import start_pipeline_task

        mock_pipeline_run = MagicMock()
        mock_pipeline_run.trace_id = "t-789"
        mock_pipeline_run.run_id = "r-789"
        mock_orchestrator_cls.return_value.start_pipeline.return_value = mock_pipeline_run

        async_result = start_pipeline_task.apply(
            kwargs={
                "payload": {"name": "test"},
                "source": "alertmanager",
                "trace_id": "t-orig",
                "environment": "production",
            }
        )
        result = async_result.get()

        assert result == {
            "status": "queued",
            "trace_id": "t-789",
            "run_id": "r-789",
        }
        mock_orchestrator_cls.return_value.start_pipeline.assert_called_once_with(
            payload={"name": "test"},
            source="alertmanager",
            trace_id="t-orig",
            environment="production",
        )
        mock_run_task.delay.assert_called_once_with(
            payload={"name": "test"},
            source="alertmanager",
            trace_id="t-789",
            environment="production",
        )
