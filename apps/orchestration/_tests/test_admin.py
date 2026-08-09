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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        cls.incident = Incident.objects.create(
            title="Test Incident",
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

    def test_pipeline_run_detail_shows_flow(self):
        run = self.pipeline_run
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
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def setUp(self):
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

    def test_pipeline_definition_changelist_shows_channel_count(self):
        from django.contrib.admin.sites import site

        from apps.notify.models import NotificationChannel
        from apps.orchestration.admin import PipelineDefinitionAdmin
        from apps.orchestration.models import PipelineDefinition

        pd = PipelineDefinition.objects.create(name="routed", match=[], priority=5)
        ch = NotificationChannel.objects.create(
            name="ops", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        pd.channels.add(ch)
        response = self.client.get("/admin/orchestration/pipelinedefinition/")
        assert response.status_code == 200
        assert "routed" in response.content.decode()
        # channel_count returns the actual number of wired channels.
        admin_obj = PipelineDefinitionAdmin(PipelineDefinition, site)
        assert admin_obj.channel_count(pd) == 1

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
        for col in ("run_id", "source", "node", "origin", "status", "age", "stuck"):
            self.assertIn(col, list_display)

    def test_actions_defined(self):
        actions = self._admin().actions
        self.assertIn("drain_selected", actions)
        self.assertIn("reclaim_stuck", actions)

    def test_age_returns_human_string(self):
        from apps.orchestration.models import InboxItem

        item = InboxItem(trace_id="t", run_id="r", created_at=timezone.now())
        self.assertIsInstance(self._admin().age(item), str)

    def test_stuck_display_delegates_to_model(self):
        from unittest.mock import MagicMock

        obj = MagicMock()
        obj.is_stuck.return_value = True
        self.assertTrue(self._admin().stuck(obj))
        obj.is_stuck.assert_called_once()

    @staticmethod
    def _queryset(rows=None, count=None, pks=None):
        """Build a fake queryset supporting count(), iteration, and values_list()."""
        from unittest.mock import MagicMock

        qs = MagicMock()
        qs.count.return_value = len(rows) if count is None and rows is not None else count
        if rows is not None:
            qs.__iter__.return_value = iter(rows)
        if pks is not None:
            qs.values_list.return_value = pks
        return qs

    def test_drain_selected_calls_helper_per_row(self):
        from unittest.mock import MagicMock, patch

        from django.contrib import messages

        admin_obj = self._admin()
        rows = [MagicMock(run_id="a"), MagicMock(run_id="b")]
        qs = self._queryset(rows=rows)
        with patch("apps.orchestration.inbox.drain_run", return_value=1) as mock_drain:
            with patch.object(admin_obj, "message_user") as mock_msg:
                admin_obj.drain_selected(request=MagicMock(), queryset=qs)
        called_ids = [c.args[0] for c in mock_drain.call_args_list]
        self.assertEqual(called_ids, ["a", "b"])
        mock_msg.assert_called_once()
        self.assertEqual(mock_msg.call_args.kwargs["level"], messages.SUCCESS)

    def test_drain_selected_isolates_row_errors(self):
        from unittest.mock import MagicMock, patch

        from django.contrib import messages

        from apps.orchestration.models import PipelineRun

        admin_obj = self._admin()
        rows = [MagicMock(run_id="ok1"), MagicMock(run_id="bad"), MagicMock(run_id="ok2")]
        qs = self._queryset(rows=rows)

        def fake_drain(run_id):
            if run_id == "bad":
                # A row claimed/deleted between listing and action.
                raise PipelineRun.DoesNotExist()
            return 1

        with patch("apps.orchestration.inbox.drain_run", side_effect=fake_drain):
            with patch.object(admin_obj, "message_user") as mock_msg:
                # Must not raise (no 500) even though a row fails.
                admin_obj.drain_selected(request=MagicMock(), queryset=qs)
        mock_msg.assert_called_once()
        message = mock_msg.call_args.args[1]
        self.assertIn("Drained 2", message)
        self.assertIn("1 failed", message)
        self.assertEqual(mock_msg.call_args.kwargs["level"], messages.ERROR)

    def test_drain_selected_refuses_oversized_selection(self):
        from unittest.mock import MagicMock, patch

        from django.contrib import messages

        admin_obj = self._admin()
        qs = self._queryset(count=admin_obj.max_drain_selection + 1)
        with patch("apps.orchestration.inbox.drain_run") as mock_drain:
            with patch.object(admin_obj, "message_user") as mock_msg:
                admin_obj.drain_selected(request=MagicMock(), queryset=qs)
        mock_drain.assert_not_called()
        mock_msg.assert_called_once()
        self.assertEqual(mock_msg.call_args.kwargs["level"], messages.WARNING)

    def test_reclaim_stuck_scopes_to_selected_pks(self):
        from unittest.mock import MagicMock, patch

        admin_obj = self._admin()
        qs = self._queryset(pks=[7, 8])
        with patch("apps.orchestration.inbox.reclaim_stuck", return_value=3) as mock_reclaim:
            with patch.object(admin_obj, "message_user") as mock_msg:
                admin_obj.reclaim_stuck(request=MagicMock(), queryset=qs)
        mock_reclaim.assert_called_once_with(pks=[7, 8])
        mock_msg.assert_called_once()


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
