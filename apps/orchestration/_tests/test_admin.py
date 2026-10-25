import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import (
    InboxItem,
    PipelineDefinition,
    PipelineRun,
    PipelineStatus,
    StageExecution,
    StageStatus,
)


class TestMonitoringAdminSite(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        cls._create_dashboard_data()

    def setUp(self):
        self.client.login(username="admin", password="password")

    @classmethod
    def _create_dashboard_data(cls):
        """Create sample data for dashboard tests."""
        # Active incidents
        Incident.objects.create(severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN)
        Incident.objects.create(severity=AlertSeverity.WARNING, status=IncidentStatus.OPEN)
        Incident.objects.create(severity=AlertSeverity.INFO, status=IncidentStatus.CLOSED)

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

    def test_dashboard_contains_active_incidents(self):
        response = self.client.get("/admin/")
        assert response.status_code == 200
        assert "active_incidents" in response.context

    def test_dashboard_contains_pipeline_health(self):
        response = self.client.get("/admin/")
        assert "pipeline_health" in response.context
        health = response.context["pipeline_health"]
        assert health["headline"] == "66.7% of runs finished"
        assert [(row["label"], row["count"]) for row in health["rows"]] == [
            ("Finished", "2"),
            ("Failed", "1"),
            ("Still going", "0"),
        ]

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
        assert "failure_reasons" in response.context
        assert "provider_usage" in response.context

    def test_dashboard_renders_panels(self):
        response = self.client.get("/admin/")
        content = response.content.decode()
        assert "Active Incidents" in content
        assert "Pipeline Health" in content


class TestPipelineTracing(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        cls.incident = Incident.objects.create(
            severity=AlertSeverity.CRITICAL,
            status=IncidentStatus.OPEN,
        )
        cls.alert = Alert.objects.create(
            fingerprint="fp-1",
            source="prometheus",
            name="HighCPU",
            severity=AlertSeverity.CRITICAL,
            status=AlertStatus.FIRING,
            incident=cls.incident,
            started_at=timezone.now(),
        )
        cls.pipeline_run = PipelineRun.objects.create(
            trace_id="trace-abc",
            run_id="run-abc",
            status=PipelineStatus.CHECKED,
            current_stage="check",
            incident=cls.incident,
        )
        StageExecution.objects.create(
            pipeline_run=cls.pipeline_run,
            stage="ingest",
            status=StageStatus.SUCCEEDED,
            attempt=1,
        )
        StageExecution.objects.create(
            pipeline_run=cls.pipeline_run,
            stage="check",
            status=StageStatus.RUNNING,
            attempt=1,
        )

    def setUp(self):
        self.client.login(username="admin", password="password")

    def test_alert_search_by_trace_id(self):
        """AlertAdmin should support searching by fingerprint."""
        response = self.client.get("/admin/alerts/alert/?q=fp-1")
        assert response.status_code == 200

    def test_check_run_pipeline_link(self):
        cr = CheckRun.objects.create(
            checker_name="cpu",
            hostname="srv1",
            status=CheckStatus.OK,
            trace_id="trace-xyz",
        )
        response = self.client.get(f"/admin/checkers/checkrun/{cr.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "trace-xyz" in content


class TestPipelineRunAdminFilters(SimpleTestCase):
    """PipelineRunAdmin exposes node/origin/status for filtering and display."""

    def _admin(self):
        from apps.orchestration.admin import PipelineRunAdmin
        from apps.orchestration.models import PipelineRun

        return PipelineRunAdmin(PipelineRun, admin.site)

    def test_list_filter_has_node_origin_status(self):
        list_filter = self._admin().list_filter
        self.assertIn("node", list_filter)
        self.assertIn("origin", list_filter)
        self.assertIn("status", list_filter)

    def test_list_display_has_node_origin_status(self):
        list_display = self._admin().list_display
        self.assertIn("node", list_display)
        self.assertIn("origin", list_display)
        self.assertIn("status", list_display)


class TestInboxAdmin(SimpleTestCase):
    """InboxAdmin monitors PENDING/PROCESSING runs with drain/reclaim actions."""

    def _admin(self):
        from apps.orchestration.admin import InboxAdmin
        from apps.orchestration.models import InboxItem

        return InboxAdmin(InboxItem, admin.site)

    def test_inbox_item_is_registered(self):
        from apps.orchestration.models import InboxItem

        self.assertTrue(admin.site.is_registered(InboxItem))

    def test_no_add_permission(self):
        self.assertFalse(self._admin().has_add_permission(request=None))

    def test_ordering_is_oldest_first(self):
        self.assertEqual(self._admin().ordering, ["created_at"])

    def test_list_display_columns(self):
        list_display = self._admin().list_display
        for col in ("run_id", "status", "origin", "node", "created_at", "updated_at"):
            self.assertIn(col, list_display)


class TestInboxAdminQueryset(TestCase):
    """InboxAdmin.get_queryset returns only inbox items (via the proxy manager)."""

    def test_get_queryset_filters_to_inbox(self):
        from unittest.mock import MagicMock

        from apps.orchestration.admin import InboxAdmin
        from apps.orchestration.models import InboxItem, PipelineRun, PipelineStatus

        PipelineRun.objects.create(trace_id="t", run_id="pend", status=PipelineStatus.PENDING)
        PipelineRun.objects.create(trace_id="t", run_id="done", status=PipelineStatus.NOTIFIED)
        admin_obj = InboxAdmin(InboxItem, admin.site)
        qs = admin_obj.get_queryset(MagicMock())
        self.assertEqual(list(qs.values_list("run_id", flat=True)), ["pend"])


class TestPipelineRunChangePageRendersCrossLinks(TestCase):
    """The PipelineRun change page actually renders the node + incident links."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.force_login(self.user)

    def test_historical_run_without_an_incident_still_renders(self):
        """``PipelineRun.incident`` is required in code, nullable in the database.

        Rows predating "a run is an incident" legitimately have no subject and must
        keep loading in the admin — changelist and change page alike.
        """
        from django.urls import reverse

        run = PipelineRun.objects.create(trace_id="t", run_id="historical")
        self.assertIsNone(run.incident_id)

        changelist = self.client.get(reverse("admin:orchestration_pipelinerun_changelist"))
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, "historical")

        change = self.client.get(reverse("admin:orchestration_pipelinerun_change", args=[run.pk]))
        self.assertEqual(change.status_code, 200)


@pytest.mark.django_db
@pytest.mark.parametrize("model", [PipelineRun, StageExecution, InboxItem])
def test_run_admins_are_stock_and_read_only(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert not model_admin.get_actions(request)
    assert not hasattr(model_admin, "change_actions")


@pytest.mark.django_db
def test_a_run_change_page_still_opens(admin_client):
    run = PipelineRun.objects.create(trace_id="t", run_id="r-stock", status="failed")
    url = reverse("admin:orchestration_pipelinerun_change", args=[run.pk])
    assert admin_client.get(url).status_code == 200


@pytest.mark.django_db
def test_the_route_admin_is_stock_and_read_only(admin_client, rf, admin_user):
    PipelineDefinition.objects.create(name="listed", match=[], priority=7)
    model_admin = admin.site._registry[PipelineDefinition]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    response = admin_client.get(reverse("admin:orchestration_pipelinedefinition_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()
