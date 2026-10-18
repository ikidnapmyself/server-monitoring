import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import (
    InboxItem,
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


class TestPrettifyJson(SimpleTestCase):
    def test_prettify_json_renders_formatted(self):
        from config.dashboard import prettify_json

        data = {"key": "value", "nested": {"a": 1}}
        result = prettify_json(data)
        assert "&quot;key&quot;" in result or '"key"' in result
        assert "<pre" in result

    def test_prettify_json_empty_dict(self):
        from config.dashboard import prettify_json

        result = prettify_json({})
        assert "{}" in result

    def test_prettify_json_none(self):
        from config.dashboard import prettify_json

        result = prettify_json(None)
        assert "-" in result


class TestJsonWidgetRendering(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
        self.client.login(username="admin", password="password")

    def test_pipeline_definition_change_page_uses_json_widget(self):
        from apps.orchestration.models import PipelineDefinition

        pd = PipelineDefinition.objects.create(name="test", match=[], is_active=True)
        response = self.client.get(f"/admin/orchestration/pipelinedefinition/{pd.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        # django-json-widget injects its CSS/JS for the match/tags JSONFields
        assert "json-editor" in content.lower() or "jsoneditor" in content.lower()

    def test_pipeline_definition_changelist_shows_channel_name(self):
        from django.contrib.admin.sites import site

        from apps.notify.models import NotificationChannel
        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        ch = NotificationChannel.objects.create(
            name="ops", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        pd = PipelineDefinition.objects.create(name="routed", match=[], priority=5, channel=ch)
        bare = PipelineDefinition.objects.create(name="unrouted", match=[], priority=6)
        response = self.client.get("/admin/orchestration/pipelinedefinition/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "routed" in body
        # The wired channel's name reaches the rendered changelist, not just the method.
        assert "ops" in body
        admin_obj = PipelineDefinitionAdmin(PipelineDefinition, site)
        # The name is the link text; an unrouted lane offers the channel list.
        assert ">ops</a>" in admin_obj.channel_name(pd)
        assert "\u2014</a>" in admin_obj.channel_name(bare)

    def test_pipeline_definition_changelist_marks_an_inactive_channel(self):
        """A deactivated channel routes nowhere, so the changelist must not imply it does."""
        from django.contrib.admin.sites import site

        from apps.notify.models import NotificationChannel
        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        ch = NotificationChannel.objects.create(
            name="dead-ops",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
            is_active=False,
        )
        pd = PipelineDefinition.objects.create(name="stale", match=[], priority=5, channel=ch)
        admin_obj = PipelineDefinitionAdmin(PipelineDefinition, site)
        rendered = admin_obj.channel_name(pd)
        assert "dead-ops" in rendered
        assert "(inactive)" in rendered
        # ...and the marker survives into the actual changelist HTML.
        body = self.client.get("/admin/orchestration/pipelinedefinition/").content.decode()
        assert "(inactive)" in body

    def test_pipeline_definition_channel_name_escapes_the_channel_name(self):
        """The name is interpolated into HTML, so it must be escaped, not trusted."""
        from django.contrib.admin.sites import site

        from apps.notify.models import NotificationChannel
        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        ch = NotificationChannel.objects.create(
            name="<b>x</b>",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
            is_active=False,
        )
        pd = PipelineDefinition.objects.create(name="xss", match=[], priority=5, channel=ch)
        rendered = PipelineDefinitionAdmin(PipelineDefinition, site).channel_name(pd)
        assert "&lt;b&gt;x&lt;/b&gt;" in rendered
        assert "<b>x</b>" not in rendered

    def test_pipeline_definition_changelist_does_not_n_plus_one_on_channel(self):
        """channel_name touches a related row; get_queryset's join keeps it constant.

        Without this, dropping select_related would still pass every other test while
        adding one query per lane to an ops page.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition

        for i in range(4):
            ch = NotificationChannel.objects.create(
                name=f"ch-{i}", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
            )
            PipelineDefinition.objects.create(name=f"lane-{i}", match=[], priority=i, channel=ch)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/admin/orchestration/pipelinedefinition/")
        assert response.status_code == 200
        # With select_related the channel arrives via a JOIN on the lane query, so no
        # query selects from the channel table on its own. Without it there would be
        # one per lane.
        standalone = [
            q["sql"]
            for q in ctx.captured_queries
            if "notify_notificationchannel" in q["sql"]
            and "orchestration_pipelinedefinition" not in q["sql"]
        ]
        assert standalone == []

    def test_save_model_defaults_created_by(self):
        from unittest.mock import MagicMock

        from django.contrib.admin.sites import site

        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        admin_obj = PipelineDefinitionAdmin(PipelineDefinition, site)
        request = MagicMock()
        request.user.username = "alice"

        obj = PipelineDefinition(name="new-pipe")
        admin_obj.save_model(request, obj, form=None, change=False)
        assert obj.created_by == "alice"

        # An explicit created_by is preserved.
        obj2 = PipelineDefinition(name="kept", created_by="bob")
        admin_obj.save_model(request, obj2, form=None, change=False)
        assert obj2.created_by == "bob"


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


class TestPipelineDefinitionStagesForm(TestCase):
    """The Add form seeds ``stages``; the data model still means empty when empty."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin2", "admin2@test.com", "password")

    def setUp(self):
        self.client.login(username="admin2", password="password")

    def test_add_form_arrives_prefilled_with_the_full_pipeline(self):
        response = self.client.get("/admin/orchestration/pipelinedefinition/add/")
        assert response.status_code == 200
        form = response.context["adminform"].form
        assert form.fields["stages"].initial == ["check", "analyze", "notify"]
        # ...and that is the value the widget actually renders, not just an attribute
        # on the field object. (Asserting on the page HTML would be vacuous: the
        # fieldset's help text quotes the same list.)
        assert form["stages"].value() == '["check", "analyze", "notify"]'

    def test_explicitly_empty_stages_still_saves_as_empty(self):
        """Seeding the form must not make an ingest-only lane inexpressible."""
        from apps.orchestration.models import PipelineDefinition

        response = self.client.post(
            "/admin/orchestration/pipelinedefinition/add/",
            {
                "name": "ingest-only-lane",
                "description": "",
                "created_by": "",
                "is_active": "on",
                "match": "[]",
                "priority": "100",
                "stages": "[]",
                "tags": "{}",
            },
        )
        assert response.status_code == 302, getattr(
            response.context.get("adminform"), "errors", response.status_code
        )
        assert PipelineDefinition.objects.get(name="ingest-only-lane").stages == []

    def test_change_form_shows_the_saved_value_not_the_seed(self):
        from apps.orchestration.models import PipelineDefinition

        pd = PipelineDefinition.objects.create(name="emptied", match=[], stages=[])
        response = self.client.get(f"/admin/orchestration/pipelinedefinition/{pd.pk}/change/")
        assert response.status_code == 200
        # The resolved bound value is what the operator sees; form.initial alone would
        # only be asserting a Django invariant, not this admin's behaviour.
        assert response.context["adminform"].form["stages"].value() == "[]"

    def test_ingest_is_rejected_through_the_admin_form(self):
        """The model-level design decision is enforced where operators actually type."""
        response = self.client.post(
            "/admin/orchestration/pipelinedefinition/add/",
            {
                "name": "bad-lane",
                "description": "",
                "created_by": "",
                "is_active": "on",
                "match": "[]",
                "priority": "100",
                "stages": '["ingest", "check"]',
                "tags": "{}",
            },
        )
        assert response.status_code == 200  # redisplayed with errors, not saved
        assert "Unknown stage" in response.content.decode()


class RoutingHelpTextDiscoverabilityTests(SimpleTestCase):
    """Every routable fact must be discoverable where operators write ``match``."""

    def _routing_description(self):
        from django.contrib.admin.sites import site

        from apps.orchestration.models import PipelineDefinition

        fieldsets = dict(
            (name, opts) for name, opts in site._registry[PipelineDefinition].fieldsets
        )
        return fieldsets["Routing"]["description"]

    def test_all_routing_fact_names_are_documented(self):
        from apps.alerts.models import Alert
        from apps.orchestration.routing import facts_from_alert

        description = self._routing_description()
        facts = facts_from_alert(Alert(source="s", severity="critical", labels={}), "manual")
        for fact in facts:
            if fact == "labels":
                continue  # exposed as the label:<k> prefix, not a bare field name
            assert fact in description, f"routing fact {fact!r} is undocumented in the admin"
        assert "label:<k>" in description

    def test_origin_values_are_documented(self):
        from apps.orchestration.models import PipelineOrigin

        description = self._routing_description()
        for value in PipelineOrigin.values:
            assert value in description


class TestChannelColumnLinksTheChannel(TestCase):
    """The lane list names a channel; naming it is how you get to it."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        self.admin = PipelineDefinitionAdmin(PipelineDefinition, AdminSite())

    def _lane(self, channel):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.create(
            name="l", priority=1, match=[], stages=["notify"], channel=channel
        )

    def test_an_active_channel_is_a_link(self):
        from apps.notify.models import NotificationChannel

        channel = NotificationChannel.objects.create(
            name="ops", driver="email", is_active=True, config={}
        )
        html = str(self.admin.channel_name(self._lane(channel)))
        assert f'<a href="/admin/notify/notificationchannel/{channel.pk}/change/">ops</a>' in html

    def test_an_inactive_channel_is_a_link_and_still_marked(self):
        from apps.notify.models import NotificationChannel

        channel = NotificationChannel.objects.create(
            name="dormant", driver="email", is_active=False, config={}
        )
        html = str(self.admin.channel_name(self._lane(channel)))
        assert f"/admin/notify/notificationchannel/{channel.pk}/change/" in html
        assert "(inactive)" in html

    def test_a_lane_with_no_channel_offers_the_channel_list(self):
        html = str(self.admin.channel_name(self._lane(None)))
        assert '<a href="/admin/notify/notificationchannel/">' in html


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
