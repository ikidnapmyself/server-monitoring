from django.test import Client, TestCase, override_settings


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
