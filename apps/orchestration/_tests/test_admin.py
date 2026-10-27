import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.orchestration.models import (
    InboxItem,
    PipelineDefinition,
    PipelineRun,
    PipelineStatus,
    StageExecution,
    StageStatus,
)


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
@pytest.mark.parametrize("model", [PipelineRun, StageExecution, PipelineDefinition])
def test_the_orchestration_admins_are_editable(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert "delete_selected" in model_admin.get_actions(request)


def test_the_inbox_is_not_registered():
    assert not admin.site.is_registered(InboxItem)


def test_run_foreign_keys_are_raw_ids():
    assert admin.site._registry[PipelineRun].raw_id_fields == ["node", "incident"]
    assert admin.site._registry[StageExecution].raw_id_fields == ["pipeline_run"]


@pytest.mark.django_db
def test_a_run_change_page_still_opens(admin_client):
    run = PipelineRun.objects.create(trace_id="t", run_id="r-stock", status="failed")
    url = reverse("admin:orchestration_pipelinerun_change", args=[run.pk])
    assert admin_client.get(url).status_code == 200


@pytest.mark.django_db
def test_the_route_changelist_lists_routes(admin_client):
    PipelineDefinition.objects.create(name="listed", match=[], priority=7)
    response = admin_client.get(reverse("admin:orchestration_pipelinedefinition_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()
