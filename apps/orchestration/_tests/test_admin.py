import pytest
from django.contrib import admin
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


@pytest.mark.django_db
class TestMonitoringAdminSite:
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

    def test_admin_index_loads(self, admin_client):
        response = admin_client.get("/admin/")
        assert response.status_code == 200


@pytest.fixture
def dashboard_data(db):
    """Create sample data for dashboard tests."""
    # Active incidents
    Incident.objects.create(
        title="CPU High", severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN
    )
    Incident.objects.create(
        title="Disk Low", severity=AlertSeverity.WARNING, status=IncidentStatus.OPEN
    )
    Incident.objects.create(title="Old", severity=AlertSeverity.INFO, status=IncidentStatus.CLOSED)

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


@pytest.mark.django_db
class TestDashboardContext:
    def test_dashboard_contains_active_incidents(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert response.status_code == 200
        assert "active_incidents" in response.context

    def test_dashboard_contains_pipeline_health(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "pipeline_health" in response.context
        health = response.context["pipeline_health"]
        assert health["total"] == 3
        assert health["successful"] == 2

    def test_dashboard_contains_recent_checks(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "recent_check_runs" in response.context
        assert len(response.context["recent_check_runs"]) == 2

    def test_dashboard_contains_failed_pipelines(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "failed_pipelines" in response.context
        assert len(response.context["failed_pipelines"]) == 1

    def test_dashboard_contains_aggregations(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        assert "top_failing_checkers" in response.context
        assert "top_error_types" in response.context
        assert "provider_usage" in response.context

    def test_dashboard_renders_panels(self, admin_client, dashboard_data):
        response = admin_client.get("/admin/")
        content = response.content.decode()
        assert "Active Incidents" in content
        assert "Pipeline Health" in content


@pytest.fixture
def pipeline_trace_data(db):
    """Create a full pipeline trace for testing."""
    incident = Incident.objects.create(
        title="Test Incident",
        severity=AlertSeverity.CRITICAL,
        status=IncidentStatus.OPEN,
    )
    alert = Alert.objects.create(
        fingerprint="fp-1",
        source="prometheus",
        name="HighCPU",
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.FIRING,
        incident=incident,
        started_at=timezone.now(),
    )
    run = PipelineRun.objects.create(
        trace_id="trace-abc",
        run_id="run-abc",
        status=PipelineStatus.CHECKED,
        current_stage="check",
        incident=incident,
    )
    StageExecution.objects.create(
        pipeline_run=run,
        stage="ingest",
        status=StageStatus.SUCCEEDED,
        attempt=1,
    )
    StageExecution.objects.create(
        pipeline_run=run,
        stage="check",
        status=StageStatus.RUNNING,
        attempt=1,
    )
    return {"incident": incident, "alert": alert, "run": run}


@pytest.mark.django_db
class TestPipelineTracing:
    def test_pipeline_run_detail_shows_flow(self, admin_client, pipeline_trace_data):
        run = pipeline_trace_data["run"]
        response = admin_client.get(f"/admin/orchestration/pipelinerun/{run.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        # Should show the pipeline flow stages
        assert "INGEST" in content
        assert "CHECK" in content
        assert "ANALYZE" in content
        assert "NOTIFY" in content

    def test_alert_search_by_trace_id(self, admin_client, pipeline_trace_data):
        """AlertAdmin should support searching by fingerprint."""
        response = admin_client.get("/admin/alerts/alert/?q=fp-1")
        assert response.status_code == 200

    def test_check_run_pipeline_link(self, admin_client, db):
        now = timezone.now()
        cr = CheckRun.objects.create(
            checker_name="cpu",
            hostname="srv1",
            status=CheckStatus.OK,
            trace_id="trace-xyz",
            executed_at=now,
        )
        response = admin_client.get(f"/admin/checkers/checkrun/{cr.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trace-xyz" in content


@pytest.mark.django_db
class TestPipelineRunObjectActions:
    def test_mark_for_retry_button(self, admin_client):
        run = PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.FAILED,
        )
        response = admin_client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_for_retry/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING

    def test_mark_failed_button(self, admin_client):
        run = PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.PENDING,
        )
        response = admin_client.post(
            f"/admin/orchestration/pipelinerun/{run.pk}/actions/mark_failed/",
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.FAILED
