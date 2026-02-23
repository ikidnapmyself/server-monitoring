from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import (
    PipelineRun,
    PipelineStatus,
    StageExecution,
    StageStatus,
)


class TestMonitoringAdminSite(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_custom_site_is_active(self):
        """The default admin.site should be our custom MonitoringAdminSite."""
        from config.admin import MonitoringAdminSite

        assert isinstance(admin.site, MonitoringAdminSite)

    def test_site_header(self):
        assert admin.site.site_header == "Server Monitoring"

    def test_site_title(self):
        assert admin.site.site_title == "Server Monitoring"

    def test_index_title(self):
        assert admin.site.index_title == "Dashboard"

    def test_admin_index_loads(self):
        response = self.client.get("/admin/")
        assert response.status_code == 200


class TestDashboardContext(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")
        self._create_dashboard_data()

    def _create_dashboard_data(self):
        """Create sample data for dashboard tests."""
        # Active incidents
        Incident.objects.create(
            title="CPU High", severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN
        )
        Incident.objects.create(
            title="Disk Low", severity=AlertSeverity.WARNING, status=IncidentStatus.OPEN
        )
        Incident.objects.create(
            title="Old", severity=AlertSeverity.INFO, status=IncidentStatus.CLOSED
        )

        # Pipeline runs (within 24h)
        now = timezone.now()
        PipelineRun.objects.create(
            trace_id="t1", run_id="r1", status=PipelineStatus.NOTIFIED, created_at=now
        )
        PipelineRun.objects.create(
            trace_id="t2", run_id="r2", status=PipelineStatus.NOTIFIED, created_at=now
        )
        PipelineRun.objects.create(
            trace_id="t3", run_id="r3", status=PipelineStatus.FAILED, created_at=now
        )

        # Check runs
        CheckRun.objects.create(
            checker_name="cpu",
            hostname="srv1",
            status=CheckStatus.CRITICAL,
            message="CPU usage at 95%",
            executed_at=now,
        )
        CheckRun.objects.create(
            checker_name="disk",
            hostname="srv1",
            status=CheckStatus.WARNING,
            message="Disk usage at 85%",
            executed_at=now,
        )

        # Analysis runs
        AnalysisRun.objects.create(
            trace_id="t1",
            pipeline_run_id="r1",
            provider="openai",
            total_tokens=500,
            status="succeeded",
        )

    def test_dashboard_contains_active_incidents(self):
        response = self.client.get("/admin/")
        assert response.status_code == 200
        assert "active_incidents" in response.context

    def test_dashboard_contains_pipeline_health(self):
        response = self.client.get("/admin/")
        assert "pipeline_health" in response.context
        health = response.context["pipeline_health"]
        assert health["total"] == 3
        assert health["successful"] == 2

    def test_dashboard_contains_recent_checks(self):
        response = self.client.get("/admin/")
        assert "recent_check_runs" in response.context
        assert len(response.context["recent_check_runs"]) == 2

    def test_dashboard_contains_failed_pipelines(self):
        response = self.client.get("/admin/")
        assert "failed_pipelines" in response.context
        assert len(response.context["failed_pipelines"]) == 1

    def test_dashboard_contains_aggregations(self):
        response = self.client.get("/admin/")
        assert "top_failing_checkers" in response.context
        assert "top_error_types" in response.context
        assert "provider_usage" in response.context

    def test_dashboard_renders_panels(self):
        response = self.client.get("/admin/")
        content = response.content.decode()
        assert "Active Incidents" in content
        assert "Pipeline Health" in content


class TestPipelineTracing(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")
        self._create_pipeline_trace_data()

    def _create_pipeline_trace_data(self):
        """Create a full pipeline trace for testing."""
        self.incident = Incident.objects.create(
            title="Test Incident",
            severity=AlertSeverity.CRITICAL,
            status=IncidentStatus.OPEN,
        )
        self.alert = Alert.objects.create(
            fingerprint="fp-1",
            source="prometheus",
            name="HighCPU",
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.FIRING,
            incident=self.incident,
            started_at=timezone.now(),
        )
        self.run = PipelineRun.objects.create(
            trace_id="trace-abc",
            run_id="run-abc",
            status=PipelineStatus.CHECKED,
            current_stage="check",
            incident=self.incident,
        )
        StageExecution.objects.create(
            pipeline_run=self.run,
            stage="ingest",
            status=StageStatus.SUCCEEDED,
            attempt=1,
        )
        StageExecution.objects.create(
            pipeline_run=self.run,
            stage="check",
            status=StageStatus.RUNNING,
            attempt=1,
        )

    def test_pipeline_run_detail_shows_flow(self):
        run = self.run
        response = self.client.get(f"/admin/orchestration/pipelinerun/{run.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        # Should show the pipeline flow stages
        assert "INGEST" in content
        assert "CHECK" in content
        assert "ANALYZE" in content
        assert "NOTIFY" in content

    def test_alert_search_by_trace_id(self):
        """AlertAdmin should support searching by fingerprint."""
        response = self.client.get("/admin/alerts/alert/?q=fp-1")
        assert response.status_code == 200

    def test_check_run_pipeline_link(self):
        now = timezone.now()
        cr = CheckRun.objects.create(
            checker_name="cpu",
            hostname="srv1",
            status=CheckStatus.OK,
            trace_id="trace-xyz",
            executed_at=now,
        )
        response = self.client.get(f"/admin/checkers/checkrun/{cr.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trace-xyz" in content


class TestPipelineRunObjectActions(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_mark_for_retry_button(self):
        run = PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.FAILED,
        )
        response = self.client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_for_retry/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING

    def test_mark_failed_button(self):
        run = PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.PENDING,
        )
        response = self.client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_failed/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.FAILED


class TestPrettifyJson(SimpleTestCase):
    def test_prettify_json_renders_formatted(self):
        from config.admin import prettify_json

        data = {"key": "value", "nested": {"a": 1}}
        result = prettify_json(data)
        assert "&quot;key&quot;" in result or '"key"' in result
        assert "<pre" in result

    def test_prettify_json_empty_dict(self):
        from config.admin import prettify_json

        result = prettify_json({})
        assert "{}" in result

    def test_prettify_json_none(self):
        from config.admin import prettify_json

        result = prettify_json(None)
        assert "-" in result


class TestJsonWidgetRendering(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_pipeline_definition_config_uses_json_widget(self):
        from apps.orchestration.models import PipelineDefinition

        pd = PipelineDefinition.objects.create(
            name="test",
            config={"stages": ["ingest"]},
            is_active=True,
        )
        response = self.client.get(f"/admin/orchestration/pipelinedefinition/{pd.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        # django-json-widget injects its CSS/JS
        assert "json-editor" in content.lower() or "jsoneditor" in content.lower()

    def test_stage_execution_snapshot_pretty(self):
        from apps.orchestration.models import (
            PipelineRun,
            StageExecution,
            StageStatus,
        )

        run = PipelineRun.objects.create(trace_id="t1", run_id="r1")
        se = StageExecution.objects.create(
            pipeline_run=run,
            stage="ingest",
            status=StageStatus.SUCCEEDED,
            attempt=1,
            output_snapshot={"result": "ok", "items": [1, 2, 3]},
        )
        response = self.client.get(f"/admin/orchestration/stageexecution/{se.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "<pre" in content
