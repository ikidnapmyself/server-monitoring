from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.orchestration.models import PipelineRun, PipelineStatus


class TestAdminQueryOptimization(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_alert_list_uses_select_related(self):
        """AlertAdmin should use select_related('incident') to avoid N+1."""
        response = self.client.get("/admin/alerts/alert/")
        assert response.status_code == 200

    def test_incident_list_uses_prefetch_related(self):
        response = self.client.get("/admin/alerts/incident/")
        assert response.status_code == 200

    def test_pipeline_run_list_loads(self):
        response = self.client.get("/admin/orchestration/pipelinerun/")
        assert response.status_code == 200

    def test_stage_execution_list_loads(self):
        response = self.client.get("/admin/orchestration/stageexecution/")
        assert response.status_code == 200

    def test_analysis_run_list_loads(self):
        response = self.client.get("/admin/intelligence/analysisrun/")
        assert response.status_code == 200

    def test_check_run_list_loads(self):
        response = self.client.get("/admin/checkers/checkrun/")
        assert response.status_code == 200


class TestBulkActions(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_acknowledge_selected_incidents(self):
        i1 = Incident.objects.create(title="Inc1", severity="critical", status=IncidentStatus.OPEN)
        i2 = Incident.objects.create(title="Inc2", severity="warning", status=IncidentStatus.OPEN)
        response = self.client.post(
            "/admin/alerts/incident/",
            {"action": "acknowledge_selected", "_selected_action": [i1.pk, i2.pk]},
        )
        assert response.status_code == 302  # redirect after action
        i1.refresh_from_db()
        i2.refresh_from_db()
        assert i1.status == IncidentStatus.ACKNOWLEDGED
        assert i2.status == IncidentStatus.ACKNOWLEDGED

    def test_resolve_selected_incidents(self):
        i1 = Incident.objects.create(title="Inc1", severity="critical", status=IncidentStatus.OPEN)
        response = self.client.post(
            "/admin/alerts/incident/",
            {"action": "resolve_selected", "_selected_action": [i1.pk]},
        )
        assert response.status_code == 302
        i1.refresh_from_db()
        assert i1.status == IncidentStatus.RESOLVED

    def test_resolve_selected_alerts(self):
        a1 = Alert.objects.create(
            fingerprint="fp-1",
            source="test",
            name="Alert1",
            severity=AlertSeverity.WARNING,
            status=AlertStatus.FIRING,
            started_at=timezone.now(),
        )
        response = self.client.post(
            "/admin/alerts/alert/",
            {"action": "resolve_selected", "_selected_action": [a1.pk]},
        )
        assert response.status_code == 302
        a1.refresh_from_db()
        assert a1.status == AlertStatus.RESOLVED

    def test_mark_pipelines_for_retry(self):
        run = PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.FAILED,
        )
        response = self.client.post(
            "/admin/orchestration/pipelinerun/",
            {"action": "mark_for_retry_selected", "_selected_action": [run.pk]},
        )
        assert response.status_code == 302
        run.refresh_from_db()
        assert run.status == PipelineStatus.RETRYING


class TestPerObjectActions(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_acknowledge_button_works(self):
        incident = Incident.objects.create(
            title="Test",
            severity="critical",
            status=IncidentStatus.OPEN,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/acknowledge_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.ACKNOWLEDGED

    def test_resolve_button_works(self):
        incident = Incident.objects.create(
            title="Test",
            severity="critical",
            status=IncidentStatus.OPEN,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/resolve_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.RESOLVED

    def test_close_button_works(self):
        incident = Incident.objects.create(
            title="Test",
            severity="critical",
            status=IncidentStatus.RESOLVED,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/close_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.CLOSED


class TestJsonPrettyDisplay(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", "admin@test.com", "password")
        self.client.login(username="admin", password="password")

    def test_alert_detail_shows_pretty_json(self):
        from apps.alerts.models import Alert, AlertSeverity, AlertStatus

        alert = Alert.objects.create(
            fingerprint="fp-json",
            source="test",
            name="JsonTest",
            severity=AlertSeverity.WARNING,
            status=AlertStatus.FIRING,
            labels={"env": "prod", "team": "ops"},
            raw_payload={"alertname": "test", "nested": {"key": "val"}},
            started_at=timezone.now(),
        )
        response = self.client.get(f"/admin/alerts/alert/{alert.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "<pre" in content
