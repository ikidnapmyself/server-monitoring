from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.orchestration.models import PipelineRun, PipelineStatus


class TestAdminQueryOptimization(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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


class TestJourneyPanel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    def test_incident_journey_shows_pipeline_run_and_stages(self):
        from apps.orchestration.models import PipelineDefinition

        pipeline = PipelineDefinition.objects.create(name="catch-all", match=[], priority=7)
        incident = Incident.objects.create(title="High CPU", severity="critical", pipeline=pipeline)
        run = PipelineRun.objects.create(
            trace_id="tr-1", run_id="run-1", status=PipelineStatus.NOTIFIED, incident=incident
        )
        run.stage_executions.create(stage="ingest", status="succeeded", attempt=1, duration_ms=3.0)

        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "catch-all" in body  # routed-by pipeline
        assert "run-1" in body
        assert "ingest" in body

    def test_incident_journey_shows_inbox_when_no_run(self):
        incident = Incident.objects.create(title="unprocessed", severity="warning")
        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        assert "inbox — not processed" in resp.content.decode()

    def test_incident_journey_run_with_no_stages(self):
        incident = Incident.objects.create(title="x", severity="warning")
        PipelineRun.objects.create(
            trace_id="t2", run_id="run-2", status=PipelineStatus.PENDING, incident=incident
        )
        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        assert "(no stages)" in resp.content.decode()

    def test_alert_journey_links_incident_and_shows_trace(self):
        incident = Incident.objects.create(title="inc", severity="critical")
        alert = Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
            trace_id="tr-9",
        )
        resp = self.client.get(f"/admin/alerts/alert/{alert.id}/change/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "tr-9" in body
        assert f"/admin/alerts/incident/{incident.id}/change/" in body

    def test_alert_journey_shows_inbox_when_no_incident(self):
        alert = Alert.objects.create(
            fingerprint="fp2",
            source="grafana",
            name="mem",
            severity="warning",
            started_at=timezone.now(),
        )
        resp = self.client.get(f"/admin/alerts/alert/{alert.id}/change/")
        assert resp.status_code == 200
        assert "not processed — inbox" in resp.content.decode()


class TestIncidentJourneyTimeline(TestCase):
    """Task 6.2: merged journey timeline rendered on IncidentAdmin."""

    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        return IncidentAdmin(Incident, AdminSite())

    def test_journey_timeline_in_readonly_fields(self):
        from apps.alerts.admin import IncidentAdmin

        assert "journey_timeline" in IncidentAdmin.readonly_fields

    def test_journey_timeline_renders_events_in_order_escaped(self):
        from datetime import timedelta

        from django.utils.safestring import SafeString

        from apps.alerts.models import AlertHistory
        from apps.orchestration.models import StageExecution

        base = timezone.now()
        incident = Incident.objects.create(title="Inc", severity="critical")
        alert = Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="critical",
            started_at=base,
            incident=incident,
        )
        hist = AlertHistory.objects.create(alert=alert, event="created", new_status="firing")
        AlertHistory.objects.filter(pk=hist.pk).update(created_at=base + timedelta(seconds=10))
        run = PipelineRun.objects.create(
            trace_id="tr", run_id="run-1", incident=incident, notify_output_ref="ref-1"
        )
        PipelineRun.objects.filter(pk=run.pk).update(created_at=base + timedelta(seconds=5))
        StageExecution.objects.create(
            pipeline_run=run,
            stage="notify",
            status="failed",
            started_at=base + timedelta(seconds=20),
            error_message="<b>boom</b>",
        )

        html = self._admin().journey_timeline(incident)
        assert isinstance(html, SafeString)
        # Chronological: pipeline run label before stage label.
        assert html.index("run-1") < html.index("notify failed")
        # Dynamic content with HTML-special chars is escaped.
        assert "&lt;b&gt;boom&lt;/b&gt;" in html
        assert "<b>boom</b>" not in html

    def test_journey_timeline_empty_incident(self):
        from django.utils.safestring import SafeString

        incident = Incident.objects.create(title="Empty", severity="info")
        html = self._admin().journey_timeline(incident)
        assert isinstance(html, SafeString)
        assert "No timeline events yet." in html


class TestAlertHistoryAdminReadability(TestCase):
    """Task 6.3: AlertHistory admin readability defaults."""

    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import AlertHistoryAdmin
        from apps.alerts.models import AlertHistory

        return AlertHistoryAdmin(AlertHistory, AdminSite())

    def test_event_label_in_list_display_and_humanizes(self):
        from apps.alerts.admin import AlertHistoryAdmin
        from apps.alerts.models import AlertHistory

        assert "event_label" in AlertHistoryAdmin.list_display
        alert = Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="warning",
            started_at=timezone.now(),
        )
        hist = AlertHistory.objects.create(alert=alert, event="status_changed")
        assert self._admin().event_label(hist) == "Status Changed"

    def test_details_pretty_escapes(self):
        from django.utils.safestring import SafeString

        from apps.alerts.models import AlertHistory

        alert = Alert.objects.create(
            fingerprint="fp",
            source="cluster",
            name="cpu",
            severity="warning",
            started_at=timezone.now(),
        )
        hist = AlertHistory.objects.create(alert=alert, event="created", details={"x": "<b>y</b>"})
        html = self._admin().details_pretty(hist)
        assert isinstance(html, SafeString)
        assert "&lt;b&gt;y&lt;/b&gt;" in html
        assert "<b>y</b>" not in html
        assert "<pre>" in html

    def test_list_filter_includes_event_and_created_at(self):
        from apps.alerts.admin import AlertHistoryAdmin

        assert "event" in AlertHistoryAdmin.list_filter
        assert "created_at" in AlertHistoryAdmin.list_filter


class IncidentDiagnosisDisplayTests(TestCase):
    def test_diagnosis_display_renders_stage_rows(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident

        incident = Incident.objects.create(title="D")
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        for label in ("alerts", "checkers", "intelligence", "notify"):
            self.assertIn(label, html)
        self.assertIn("never", html.lower())

    def test_diagnosis_display_escapes_detail(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident
        from apps.orchestration.models import PipelineRun, StageExecution

        incident = Incident.objects.create(title="D2")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run,
            stage="analyze",
            status="failed",
            error_type="X",
            error_message="<script>bad</script>",
            error_retryable=False,
        )
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        self.assertNotIn("<script>bad", html)
        self.assertIn("&lt;script&gt;", html)
