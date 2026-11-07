from django.test import Client, TestCase, override_settings


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
