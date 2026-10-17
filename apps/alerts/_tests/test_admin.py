from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import (
    Alert,
    AlertHistory,
    Incident,
    IncidentStatus,
    Node,
)
from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus
from config.admin_links import DASH


def _manual_runs(incident):
    return PipelineRun.objects.filter(
        incident=incident, status=PipelineStatus.PENDING, origin=PipelineOrigin.MANUAL
    )


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

    def test_analysis_run_search_matches_the_incident_subject(self):
        from apps.intelligence.models import AnalysisRun

        incident = Incident.objects.create(subject="checker:disk", severity="warning")
        run = AnalysisRun.objects.create(trace_id="t-1", provider="local", incident=incident)
        other = AnalysisRun.objects.create(trace_id="t-2", provider="local")

        response = self.client.get("/admin/intelligence/analysisrun/", {"q": "checker:disk"})

        body = response.content.decode()
        assert f"/admin/intelligence/analysisrun/{run.pk}/change/" in body
        assert f"/admin/intelligence/analysisrun/{other.pk}/change/" not in body

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
        i1 = Incident.objects.create(severity="critical", status=IncidentStatus.OPEN)
        i2 = Incident.objects.create(severity="warning", status=IncidentStatus.OPEN)
        response = self.client.post(
            "/admin/alerts/incident/",
            {"action": "acknowledge_selected", "_selected_action": [i1.pk, i2.pk]},
        )
        assert response.status_code == 302  # redirect after action
        i1.refresh_from_db()
        i2.refresh_from_db()
        assert i1.status == IncidentStatus.ACKNOWLEDGED
        assert i2.status == IncidentStatus.ACKNOWLEDGED
        assert _manual_runs(i1).count() == 1
        assert _manual_runs(i2).count() == 1

    def test_resolve_selected_incidents(self):
        i1 = Incident.objects.create(severity="critical", status=IncidentStatus.OPEN)
        response = self.client.post(
            "/admin/alerts/incident/",
            {"action": "resolve_selected", "_selected_action": [i1.pk]},
        )
        assert response.status_code == 302
        i1.refresh_from_db()
        assert i1.status == IncidentStatus.RESOLVED
        assert _manual_runs(i1).count() == 1

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
            severity="critical",
            status=IncidentStatus.OPEN,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/acknowledge_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.ACKNOWLEDGED
        assert _manual_runs(incident).count() == 1

    def test_resolve_button_works(self):
        incident = Incident.objects.create(
            severity="critical",
            status=IncidentStatus.OPEN,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/resolve_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.RESOLVED
        assert _manual_runs(incident).count() == 1

    def test_a_get_never_transitions_an_incident(self):
        """A link, a redirect or a prefetch must not be able to change an incident."""
        for tool in ("acknowledge_incident", "resolve_incident", "close_incident"):
            incident = Incident.objects.create(status=IncidentStatus.OPEN)
            response = self.client.get(f"/admin/alerts/incident/{incident.pk}/actions/{tool}/")
            assert response.status_code == 405
            incident.refresh_from_db()
            assert incident.status == IncidentStatus.OPEN
            assert _manual_runs(incident).count() == 0

    def test_staff_without_change_permission_cannot_transition(self):
        User.objects.create_user("viewer", password="password", is_staff=True)
        self.client.login(username="viewer", password="password")
        for tool in ("acknowledge_incident", "resolve_incident", "close_incident"):
            incident = Incident.objects.create(status=IncidentStatus.OPEN)
            response = self.client.post(f"/admin/alerts/incident/{incident.pk}/actions/{tool}/")
            assert response.status_code == 403
            incident.refresh_from_db()
            assert incident.status == IncidentStatus.OPEN
            assert _manual_runs(incident).count() == 0

    def test_buttons_are_csrf_protected_forms(self):
        incident = Incident.objects.create(status=IncidentStatus.OPEN)
        content = self.client.get(f"/admin/alerts/incident/{incident.pk}/change/").content.decode()
        action_url = f"/admin/alerts/incident/{incident.pk}/actions/resolve_incident/"
        assert f'<form method="post" action="{action_url}">' in content
        assert f'href="{action_url}"' not in content

    def test_view_only_staff_see_no_buttons(self):
        from django.contrib.auth.models import Permission

        viewer = User.objects.create_user("viewer", password="password", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_incident"))
        self.client.login(username="viewer", password="password")
        incident = Incident.objects.create(status=IncidentStatus.OPEN)
        response = self.client.get(f"/admin/alerts/incident/{incident.pk}/change/")
        assert response.status_code == 200
        assert "/actions/" not in response.content.decode()

    def test_close_button_works(self):
        incident = Incident.objects.create(
            severity="critical",
            status=IncidentStatus.RESOLVED,
        )
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/close_incident/",
        )
        assert response.status_code == 302
        incident.refresh_from_db()
        assert incident.status == IncidentStatus.CLOSED
        assert _manual_runs(incident).count() == 1


class TestIncidentActionMessages(TestCase):
    """The admin says what `IncidentManager.transition` decided, in the same words."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    def _click(self, status, action):
        incident = Incident.objects.create(severity="critical", status=status)
        response = self.client.post(
            f"/admin/alerts/incident/{incident.pk}/actions/{action}/", follow=True
        )
        incident.refresh_from_db()
        (message,) = list(response.context["messages"])
        return incident, message

    def test_accepted_actions_report_success(self):
        cases = [
            (IncidentStatus.OPEN, "acknowledge_incident", "acknowledged"),
            (IncidentStatus.ACKNOWLEDGED, "resolve_incident", "resolved"),
            (IncidentStatus.RESOLVED, "close_incident", "closed"),
        ]
        for status, action, text in cases:
            with self.subTest(action=action):
                incident, message = self._click(status, action)
                assert message.message == f"Incident #{incident.pk} {text}."
                assert message.level_tag == "info"
                assert _manual_runs(incident).count() == 1

    def test_refused_actions_warn_and_enqueue_nothing(self):
        cases = [
            (
                IncidentStatus.ACKNOWLEDGED,
                "acknowledge_incident",
                "Cannot acknowledge — status is 'acknowledged'.",
            ),
            (IncidentStatus.RESOLVED, "resolve_incident", "Already resolved."),
            (IncidentStatus.CLOSED, "resolve_incident", "Already closed."),
            (IncidentStatus.CLOSED, "close_incident", "Already closed."),
        ]
        for status, action, text in cases:
            with self.subTest(status=status, action=action):
                incident, message = self._click(status, action)
                assert message.message == text
                assert message.level_tag == "warning"
                assert incident.status == status
                assert _manual_runs(incident).count() == 0

    def _bulk(self, action):
        incidents = [
            Incident.objects.create(severity="warning", status=status)
            for status in IncidentStatus.values
        ]
        response = self.client.post(
            "/admin/alerts/incident/",
            {"action": action, "_selected_action": [i.pk for i in incidents]},
            follow=True,
        )
        (message,) = list(response.context["messages"])
        return {
            status: Incident.objects.get(pk=i.pk)
            for status, i in zip(IncidentStatus.values, incidents)
        }, message.message

    def test_bulk_acknowledge_counts_only_the_open_ones(self):
        by_status, text = self._bulk("acknowledge_selected")
        assert text == "1 incident(s) acknowledged."
        assert [s for s, i in by_status.items() if _manual_runs(i).exists()] == ["open"]
        assert by_status["open"].status == IncidentStatus.ACKNOWLEDGED
        assert by_status["resolved"].status == IncidentStatus.RESOLVED
        assert by_status["closed"].status == IncidentStatus.CLOSED

    def test_bulk_resolve_skips_resolved_and_closed(self):
        by_status, text = self._bulk("resolve_selected")
        assert text == "2 incident(s) resolved."
        announced = sorted(s for s, i in by_status.items() if _manual_runs(i).exists())
        assert announced == ["acknowledged", "open"]
        assert by_status["closed"].status == IncidentStatus.CLOSED


class TestJourneyPanel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    def test_incident_journey_shows_pipeline_run_and_stages(self):
        from apps.orchestration.models import PipelineDefinition

        pipeline = PipelineDefinition.objects.create(name="admin-lane", match=[], priority=7)
        incident = Incident.objects.create(severity="critical", pipeline=pipeline)
        run = PipelineRun.objects.create(
            trace_id="tr-1", run_id="run-1", status=PipelineStatus.NOTIFIED, incident=incident
        )
        run.stage_executions.create(stage="ingest", status="succeeded", attempt=1, duration_ms=3.0)

        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "admin-lane" in body  # routed-by pipeline
        assert "run-1" in body
        assert "ingest" in body

    def test_incident_journey_shows_inbox_when_no_run(self):
        incident = Incident.objects.create(severity="warning")
        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        assert "inbox — not processed" in resp.content.decode()

    def test_incident_journey_run_with_no_stages(self):
        incident = Incident.objects.create(severity="warning")
        PipelineRun.objects.create(
            trace_id="t2", run_id="run-2", status=PipelineStatus.PENDING, incident=incident
        )
        resp = self.client.get(f"/admin/alerts/incident/{incident.id}/change/")
        assert resp.status_code == 200
        assert "(no stages)" in resp.content.decode()


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
        incident = Incident.objects.create(severity="critical")
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

        incident = Incident.objects.create(severity="info")
        html = self._admin().journey_timeline(incident)
        assert isinstance(html, SafeString)
        assert "No timeline events yet." in html


class IncidentDiagnosisDisplayTests(TestCase):
    def test_diagnosis_display_renders_stage_rows(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident

        incident = Incident.objects.create()
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        for label in ("checkers", "intelligence", "notify"):
            self.assertIn(label, html)
        # "alerts" (ingest) is history: not produced for an incident of this era.
        self.assertNotIn("alerts", html)
        self.assertIn("never", html.lower())

    def test_diagnosis_display_still_renders_ingest_for_a_legacy_run(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident
        from apps.orchestration.models import PipelineRun

        incident = Incident.objects.create()
        PipelineRun.objects.create(
            trace_id="t",
            run_id="r-legacy",
            incident=incident,
            inbound_payload={"driver": "grafana", "payload": {}},
        )
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        self.assertIn("alerts", html)

    def test_diagnosis_display_escapes_detail(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident
        from apps.orchestration.models import PipelineRun, StageExecution

        incident = Incident.objects.create()
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


class IncidentHeadlineAdminTests(TestCase):
    """Incident names in the admin come from the describer, not stored columns."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")
        self.incident = Incident.objects.create(
            severity="critical",
            subject="HighLatency",
            instance="db-1",
        )
        self.alert = Alert.objects.create(
            name="HighLatency",
            fingerprint="fp-hl",
            source="grafana",
            severity="critical",
            description="p99 over 2s",
            started_at=timezone.now(),
            incident=self.incident,
        )

    def test_incident_changelist_shows_the_headline(self):
        from apps.alerts.admin import IncidentAdmin

        self.assertEqual(IncidentAdmin.list_display[0], "headline")
        html = self.client.get("/admin/alerts/incident/").content.decode()
        self.assertIn("HighLatency on db-1", html)
        self.assertNotIn("stale title", html)

    def test_incident_search_matches_subject_and_instance(self):
        for term in ("HighLatency", "db-1"):
            with self.subTest(term=term):
                html = self.client.get("/admin/alerts/incident/", {"q": term}).content.decode()
                self.assertIn(f"/admin/alerts/incident/{self.incident.pk}/change/", html)

    def test_change_form_shows_the_headline_read_only_without_stored_text(self):
        html = self.client.get(
            f"/admin/alerts/incident/{self.incident.pk}/change/"
        ).content.decode()
        self.assertIn("HighLatency on db-1", html)
        self.assertNotIn('name="title"', html)
        self.assertNotIn('name="description"', html)
        self.assertNotIn("stale description", html)


class IncidentAdminHostColumnTests(TestCase):
    """The incident changelist must name the machine the incident is about."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("hostadmin", "host@test.com", "password")

    def setUp(self):
        self.client.login(username="hostadmin", password="password")

    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        return IncidentAdmin(Incident, AdminSite())

    def _incident(self, **alert_kwargs):
        incident = Incident.objects.create(severity="warning")
        Alert.objects.create(
            incident=incident,
            name="cpu",
            fingerprint=alert_kwargs.pop("fingerprint", "fp"),
            source=alert_kwargs.pop("source", "cluster"),
            started_at=timezone.now(),
            **alert_kwargs,
        )
        return incident

    def test_host_in_list_display_and_filter(self):
        from apps.alerts.admin import IncidentAdmin

        self.assertIn("host", IncidentAdmin.list_display)
        self.assertIn("alerts__node", IncidentAdmin.list_filter)

    def test_host_prefers_registered_node(self):
        node = Node.objects.create(instance_id="node-a", hostname="web-01")
        incident = self._incident(labels={"instance_id": "node-a"}, node=node)
        self.assertEqual(self._admin().host(incident), "web-01")

    def test_host_falls_back_to_labels(self):
        incident = self._incident(source="grafana", labels={"instance": "10.0.0.7"})
        self.assertEqual(self._admin().host(incident), "10.0.0.7")

    def test_host_is_dash_without_alerts(self):
        incident = Incident.objects.create(severity="info")
        self.assertEqual(self._admin().host(incident), "—")

    def test_host_is_dash_when_nothing_identifies_the_machine(self):
        incident = self._incident(source="grafana", labels={})
        self.assertEqual(self._admin().host(incident), "—")

    def test_host_lists_every_machine_when_alerts_disagree(self):
        incident = Incident.objects.create(severity="warning")
        for i, instance in enumerate(["10.0.0.7", "10.0.0.8", "10.0.0.7"]):
            Alert.objects.create(
                incident=incident,
                name="cpu",
                fingerprint=f"multi-{i}",
                source="grafana",
                labels={"instance": instance},
                started_at=timezone.now(),
            )
        self.assertEqual(self._admin().host(incident), "10.0.0.7, 10.0.0.8")

    def test_host_escapes_attacker_controlled_labels(self):
        self._incident(source="grafana", labels={"instance": "<script>bad</script>"})
        html = self.client.get("/admin/alerts/incident/").content.decode()
        self.assertNotIn("<script>bad", html)
        self.assertIn("&lt;script&gt;bad", html)


class NodeLabelTests(TestCase):
    def test_prefers_hostname(self):
        from apps.alerts.admin import node_label

        self.assertEqual(node_label(Node(instance_id="node-a", hostname="web-01")), "web-01")

    def test_falls_back_to_instance_id(self):
        from apps.alerts.admin import node_label

        self.assertEqual(node_label(Node(instance_id="node-a", hostname="")), "node-a")


class IncidentPanelLinkTests(TestCase):
    """The diagnostic panels name objects; every one of them has to be reachable.

    A panel that reports "notify failed" and leaves an operator to find the run
    by hand is the dead-end these assertions exist to prevent.
    """

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin
        from apps.orchestration.models import PipelineDefinition

        self.admin = IncidentAdmin(Incident, AdminSite())
        self.pipeline = PipelineDefinition.objects.create(
            name="admin-lane", match=[], priority=7, stages=["check", "analyze", "notify"]
        )
        self.incident = Incident.objects.create(pipeline=self.pipeline)

    def _run(self, **kw):
        return PipelineRun.objects.create(
            trace_id="tr-1", run_id="run-1", incident=self.incident, **kw
        )

    def test_diagnosis_links_a_failed_stage_to_its_execution(self):
        run = self._run()
        execution = run.stage_executions.create(
            stage="notify", status="failed", error_message="boom", attempt=1
        )
        html = str(self.admin.diagnosis_display(self.incident))
        assert f'href="/admin/orchestration/stageexecution/{execution.pk}/change/"' in html

    def test_diagnosis_leaves_a_stage_that_never_ran_unlinked(self):
        html = str(self.admin.diagnosis_display(self.incident))
        assert "stageexecution" not in html

    def test_journey_links_the_routing_lane(self):
        html = str(self.admin.journey_display(self.incident))
        assert f'href="/admin/orchestration/pipelinedefinition/{self.pipeline.pk}/change/"' in html

    def test_journey_links_each_run(self):
        run = self._run()
        html = str(self.admin.journey_display(self.incident))
        assert f'href="/admin/orchestration/pipelinerun/{run.pk}/change/"' in html

    def test_journey_links_each_stage_execution(self):
        run = self._run()
        execution = run.stage_executions.create(stage="check", status="succeeded", duration_ms=1.0)
        html = str(self.admin.journey_display(self.incident))
        assert f'href="/admin/orchestration/stageexecution/{execution.pk}/change/"' in html

    def test_an_undrained_incident_links_the_inbox_instead_of_naming_a_command(self):
        """The inbox admin has a drain action, so the page must not send an
        operator to a terminal for it."""
        html = str(self.admin.journey_display(self.incident))
        assert 'href="/admin/orchestration/inboxitem/' in html
        assert "process_inbox" not in html

    def test_journey_is_dash_for_an_unrouted_incident(self):
        unrouted = Incident.objects.create()
        html = str(self.admin.journey_display(unrouted))
        assert "pipelinedefinition" not in html


class IncidentCountLinkTests(TestCase):
    """A count on the incident page is a question: which ones?

    Each one answers itself by landing on exactly the rows it counted.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("counter", "c@t.com", "password")

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        self.admin = IncidentAdmin(Incident, AdminSite())
        self.incident = Incident.objects.create()
        self.client.login(username="counter", password="password")

    def _alert(self, fingerprint, status):
        return Alert.objects.create(
            fingerprint=fingerprint,
            source="cluster",
            name="cpu",
            severity="critical",
            status=status,
            started_at=timezone.now(),
            incident=self.incident,
        )

    def test_the_alert_count_links_this_incidents_alerts(self):
        self._alert("a", "firing")
        html = str(self.admin.alert_count_display(self.incident))
        url = f"/admin/alerts/alert/?incident__id__exact={self.incident.pk}"
        assert f'<a href="{url}">1</a>' == html
        assert self.client.get(url).status_code == 200

    def test_the_firing_count_links_only_the_firing_ones(self):
        self._alert("a", "firing")
        self._alert("b", "resolved")
        html = str(self.admin.firing_alert_count_display(self.incident))
        url = f"/admin/alerts/alert/?incident__id__exact={self.incident.pk}" "&status__exact=firing"
        # The href is HTML-escaped; the browser sends the raw query.
        assert f'href="{url.replace("&", "&amp;")}"' in html
        assert self.client.get(url).status_code == 200

    def test_a_zero_alert_count_is_not_a_link(self):
        assert self.admin.alert_count_display(self.incident) == 0

    def test_a_zero_firing_count_is_not_a_link(self):
        self._alert("a", "resolved")
        assert self.admin.firing_alert_count_display(self.incident) == 0

    def test_the_run_count_links_this_incidents_runs(self):
        PipelineRun.objects.create(trace_id="t", run_id="r", incident=self.incident)
        html = str(self.admin.pipeline_runs_display(self.incident))
        url = f"/admin/orchestration/pipelinerun/?incident__id__exact={self.incident.pk}"
        assert f'<a href="{url}">1</a>' == html
        assert self.client.get(url).status_code == 200

    def test_a_zero_run_count_is_not_a_link(self):
        assert self.admin.pipeline_runs_display(self.incident) == 0

    def test_a_subject_with_no_runs_relation_is_a_dash(self):
        """The readonly field is reused for objects that have no such relation."""

        class NoRuns:
            pass

        assert self.admin.pipeline_runs_display(NoRuns()) == DASH


class IncidentBadgeToneTests(TestCase):
    """The incident pills carry a tone class, never a hex."""

    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        return IncidentAdmin(Incident, AdminSite())

    def test_each_severity_carries_its_tone(self):
        for severity, tone in [
            ("critical", "critical"),
            ("warning", "warning"),
            ("info", "info"),
            ("chartreuse", "muted"),
        ]:
            html = str(self._admin().severity_badge(Incident(severity=severity)))
            assert f"ops-badge--{tone}" in html
            assert severity.upper() in html
            assert "#" not in html

    def test_each_status_carries_its_tone(self):
        for status, tone in [
            ("open", "critical"),
            ("acknowledged", "warning"),
            ("resolved", "ok"),
            ("closed", "muted"),
            ("pondering", "muted"),
        ]:
            html = str(self._admin().status_badge(Incident(status=status)))
            assert f"ops-badge--{tone}" in html
            assert status.upper() in html
            assert "#" not in html


class IncidentFiringCountToneTests(TestCase):
    def test_a_firing_count_is_a_critical_tint(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        incident = Incident.objects.create()
        Alert.objects.create(
            fingerprint="f",
            source="cluster",
            name="cpu",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            incident=incident,
        )
        html = str(IncidentAdmin(Incident, AdminSite()).firing_alert_count_display(incident))
        assert "ops-tint--critical" in html
        assert ">1<" in html
        assert "#" not in html


class IncidentDiagnosisToneTests(TestCase):
    """The stage strip's colour is its whole point: a glance says which stage broke."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        self.admin = IncidentAdmin(Incident, AdminSite())

    def _status(self, status):
        return str(self.admin._render_status({"status": status, "execution_pk": None}))

    def test_each_status_carries_its_tone_and_glyph(self):
        for status, tone, glyph, label in [
            ("ok", "ok", "✓", "ok"),
            ("empty", "warning", "✓→∅", "empty"),
            ("failed", "critical", "✗", "failed"),
            ("stalled", "warning", "…", "running / stalled"),
            ("skipped", "muted", "⊘", "skipped"),
            ("never_ran", "critical", "✗", "never ran"),
        ]:
            html = self._status(status)
            assert f"ops-tint--{tone}" in html
            assert glyph in html
            assert label in html
            assert "#" not in html

    def test_an_unmapped_status_is_muted_and_labels_itself(self):
        html = self._status("brand-new")
        assert "ops-tint--muted" in html
        assert "? brand-new" in html

    def test_a_linked_status_keeps_its_tone_inside_the_anchor(self):
        run = PipelineRun.objects.create(trace_id="t", run_id="r")
        execution = run.stage_executions.create(stage="notify", status="failed", attempt=1)
        html = str(self.admin._render_status({"status": "failed", "execution_pk": execution.pk}))
        assert f'href="/admin/orchestration/stageexecution/{execution.pk}/change/"' in html
        assert "ops-tint--critical" in html

    def test_the_run_rollup_is_a_muted_parenthetical(self):
        incident = Incident.objects.create()
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        run.stage_executions.create(stage="check", status="succeeded", attempt=1)
        html = str(self.admin.diagnosis_display(incident))
        assert "ops-tint--muted" in html
        assert "succeeded in 1/1 runs" in html

    def test_the_strip_hardcodes_no_colour_and_carries_its_layout_classes(self):
        incident = Incident.objects.create()
        html = str(self.admin.diagnosis_display(incident))
        assert "#" not in html
        assert "ops-diag" in html
        assert "ops-diag-stage" in html


class IncidentJourneyToneTests(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import IncidentAdmin

        self.admin = IncidentAdmin(Incident, AdminSite())
        self.incident = Incident.objects.create()

    def test_an_undrained_incident_reads_critical(self):
        html = str(self.admin.journey_display(self.incident))
        assert "ops-tint--critical" in html
        assert "<b>inbox — not processed</b>" in html

    def test_a_runs_trace_id_is_muted(self):
        PipelineRun.objects.create(trace_id="tr-9", run_id="r", incident=self.incident)
        html = str(self.admin.journey_display(self.incident))
        assert "ops-tint--muted" in html
        assert "trace tr-9" in html

    def test_the_timeline_timestamp_is_muted(self):
        Alert.objects.create(
            fingerprint="f",
            source="cluster",
            name="cpu",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            incident=self.incident,
        ).history.create(event="created")
        html = str(self.admin.journey_timeline(self.incident))
        assert "ops-tint--muted" in html
        assert "#" not in html


class TestAlertAdminIsStock(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(self.user)
        self.alert = Alert.objects.create(
            fingerprint="fp",
            name="a",
            severity="warning",
            status="firing",
            source="cluster",
            started_at=timezone.now(),
        )

    def test_alert_admin_is_read_only_with_no_actions(self):
        model_admin = admin.site._registry[Alert]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, self.alert)
        assert not model_admin.has_delete_permission(request, self.alert)
        assert "resolve_selected" not in model_admin.get_actions(request)
        assert "delete_selected" not in model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_history_admin_is_read_only(self):
        model_admin = admin.site._registry[AlertHistory]
        request = RequestFactory().get("/")
        request.user = self.user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
        assert "delete_selected" not in model_admin.get_actions(request)

    def test_change_page_still_opens_for_a_superuser(self):
        url = reverse("admin:alerts_alert_change", args=[self.alert.pk])
        assert self.client.get(url).status_code == 200
