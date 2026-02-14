import pytest
from django.contrib import admin
from django.utils import timezone

from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import PipelineRun, PipelineStatus


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
    )
    CheckRun.objects.create(
        checker_name="disk",
        hostname="srv1",
        status=CheckStatus.WARNING,
        message="Disk usage at 85%",
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
