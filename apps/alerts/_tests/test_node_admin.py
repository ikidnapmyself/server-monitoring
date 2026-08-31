import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.utils.safestring import SafeString
from django_json_widget.widgets import JSONEditorWidget

from apps.alerts.models import Alert, Incident, Node


class NodeAdminTests(TestCase):
    def _admin(self):
        self.assertIn(Node, admin.site._registry)
        return admin.site._registry[Node]

    def test_registered_with_registry_listing(self):
        model_admin = self._admin()
        self.assertIn("instance_id", model_admin.list_display)
        self.assertIn("last_seen", model_admin.list_display)

    def test_nodes_cannot_be_added_in_admin(self):
        # Nodes are created only by the ingest path.
        self.assertFalse(self._admin().has_add_permission(None))

    def test_nodes_cannot_be_deleted_in_admin(self):
        # Deleting a Node would silently drop the operator-authored config policy.
        self.assertFalse(self._admin().has_delete_permission(None))

    def test_config_is_editable_registry_is_readonly(self):
        model_admin = self._admin()
        # config is the one operator-editable field...
        self.assertIn("config", model_admin.fields)
        self.assertNotIn("config", model_admin.readonly_fields)
        # ...while ingest-owned registry fields stay read-only.
        for registry_field in ["instance_id", "hostname", "last_source", "labels"]:
            self.assertIn(registry_field, model_admin.readonly_fields)

    def test_config_uses_json_editor_widget(self):
        overrides = self._admin().formfield_overrides
        self.assertIs(overrides[db_models.JSONField]["widget"], JSONEditorWidget)


class NodeReevaluateActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = admin.site._registry[Node]
        self.user = get_user_model().objects.create_superuser(
            username="ops", email="ops@example.com", password="pw"
        )

    def _request(self, method="get", data=None):
        request = getattr(self.factory, method)("/", data or {})
        request.user = self.user
        # message_user requires a message store on the request.
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def _firing_cpu_alert(self, node):
        return Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 42.0})},
        )

    def test_action_registered(self):
        self.assertIn("reevaluate_open_alerts", self.model_admin.change_actions)

    def test_empty_report_returns_none_with_message(self):
        node = Node.objects.create(instance_id="web-03", config={})
        request = self._request("get")
        result = self.model_admin.reevaluate_open_alerts(request, node)
        self.assertIsNone(result)
        messages = list(request._messages)
        self.assertEqual(len(messages), 1)
        self.assertIn("No open alerts", messages[0].message)

    def test_get_renders_confirmation_page(self):
        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._firing_cpu_alert(node)
        request = self._request("get")
        response = self.model_admin.reevaluate_open_alerts(request, node)
        self.assertIsInstance(response, TemplateResponse)
        response.render()
        content = response.content.decode()
        self.assertIn("cpu", content)
        self.assertIn("Confirm", content)
        # GET must not write.
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_post_confirm_applies_and_messages(self):
        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._firing_cpu_alert(node)
        request = self._request("post", {"confirm": "1"})
        result = self.model_admin.reevaluate_open_alerts(request, node)
        self.assertIsNone(result)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")
        messages = list(request._messages)
        self.assertEqual(len(messages), 1)
        self.assertIn("Resolved 1", messages[0].message)

    def test_post_without_confirm_renders_page_and_does_not_apply(self):
        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._firing_cpu_alert(node)
        request = self._request("post")  # bare POST, no confirm field
        response = self.model_admin.reevaluate_open_alerts(request, node)
        self.assertIsInstance(response, TemplateResponse)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_staff_without_change_permission_is_denied(self):
        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._firing_cpu_alert(node)
        staff = get_user_model().objects.create_user(
            username="viewer", email="viewer@example.com", password="pw", is_staff=True
        )
        request = self._request("post", {"confirm": "1"})
        request.user = staff
        with self.assertRaises(PermissionDenied):
            self.model_admin.reevaluate_open_alerts(request, node)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")


class NodePageInlineDisplaysTests(TestCase):
    """Node change page: disk sparkline, recent pipelines, latest preflight."""

    def setUp(self):
        self.model_admin = admin.site._registry[Node]

    def _disk_run(self, node, worst_percent, *, with_alert=False, offset=0):
        from apps.checkers.models import CheckRun

        alert = None
        if with_alert:
            alert = Alert.objects.create(
                fingerprint=f"disk-{node.hostname}-{offset}",
                source="cluster",
                name="disk high",
                severity="critical",
                status="firing",
                started_at=timezone.now(),
                node=node,
            )
        metrics = {} if worst_percent is None else {"worst_percent": worst_percent}
        return CheckRun.objects.create(
            checker_name="disk",
            hostname=node.hostname,
            status="ok",
            metrics=metrics,
            alert=alert,
            executed_at=timezone.now() + timezone.timedelta(minutes=offset),
        )

    def test_display_methods_in_readonly_fields(self):
        for field in ("disk_sparkline", "recent_pipelines", "latest_preflight"):
            self.assertIn(field, self.model_admin.readonly_fields)

    def test_disk_sparkline_renders_svg_with_alert_marker(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._disk_run(node, 40.0, offset=0)
        self._disk_run(node, 85.0, with_alert=True, offset=1)
        result = self.model_admin.disk_sparkline(node)
        self.assertIsInstance(result, SafeString)
        self.assertIn("<svg", result)
        self.assertIn("circle", result)

    def test_disk_sparkline_skips_missing_worst_percent(self):
        node = Node.objects.create(instance_id="web-04", hostname="web-04")
        self._disk_run(node, None, offset=0)  # no worst_percent
        self._disk_run(node, "bad", offset=1)  # non-numeric
        self._disk_run(node, 50.0, offset=2)
        result = self.model_admin.disk_sparkline(node)
        self.assertIn("<svg", result)

    def test_disk_sparkline_empty_history(self):
        node = Node.objects.create(instance_id="web-05", hostname="web-05")
        result = self.model_admin.disk_sparkline(node)
        self.assertEqual(result, "No disk history.")

    def test_disk_sparkline_uses_recent_window_not_oldest(self):
        # >50 runs: only the most-recent run carries an alert marker. The recent
        # window (last 50) must include it; the old ascending [:50] would have
        # dropped it (it selected runs 0-49, never the newest).
        recent_node = Node.objects.create(instance_id="web-12", hostname="web-12")
        for i in range(60):
            self._disk_run(recent_node, float(i), with_alert=(i == 59), offset=i)
        recent_result = self.model_admin.disk_sparkline(recent_node)
        # "#d33" is the alert marker fill from render_sparkline — present only if
        # the newest (alert-bearing) run is within the plotted window.
        self.assertIn("#d33", recent_result)

        # Conversely, an alert only on the OLDEST run must be dropped by the window.
        old_node = Node.objects.create(instance_id="web-13", hostname="web-13")
        for i in range(60):
            self._disk_run(old_node, float(i), with_alert=(i == 0), offset=i)
        old_result = self.model_admin.disk_sparkline(old_node)
        self.assertNotIn("#d33", old_result)

    def test_recent_pipelines_lists_runs_newest_first_with_links(self):
        from apps.orchestration.models import PipelineRun

        node = Node.objects.create(instance_id="web-06", hostname="web-06")
        old = PipelineRun.objects.create(trace_id="t1", run_id="run-old", node=node)
        new = PipelineRun.objects.create(trace_id="t2", run_id="run-new", node=node)
        # Set created_at explicitly so ordering can't tie at microsecond resolution.
        PipelineRun.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        PipelineRun.objects.filter(pk=new.pk).update(created_at=timezone.now())
        result = self.model_admin.recent_pipelines(node)
        self.assertIsInstance(result, SafeString)
        self.assertIn("run-old", result)
        self.assertIn("run-new", result)
        self.assertIn(f"/admin/orchestration/pipelinerun/{new.pk}/change/", result)
        # newest first
        self.assertLess(result.index("run-new"), result.index("run-old"))

    def test_recent_pipelines_escapes_content(self):
        from apps.orchestration.models import PipelineRun

        node = Node.objects.create(instance_id="web-07", hostname="web-07")
        PipelineRun.objects.create(
            trace_id="t", run_id="<script>", node=node, origin="incoming_webhook"
        )
        result = self.model_admin.recent_pipelines(node)
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_recent_pipelines_empty(self):
        node = Node.objects.create(instance_id="web-08", hostname="web-08")
        result = self.model_admin.recent_pipelines(node)
        self.assertIsInstance(result, str)
        self.assertNotIn("/admin/orchestration/", result)

    def test_latest_preflight_shows_matching_run(self):
        from apps.checkers.models import PreflightRun

        node = Node.objects.create(instance_id="web-09", hostname="web-09")
        PreflightRun.objects.create(
            instance_id="web-09", passed=5, warnings=1, errors=0, overall_status="warn"
        )
        result = self.model_admin.latest_preflight(node)
        self.assertIsInstance(result, SafeString)
        self.assertIn("warn", result)

    def test_latest_preflight_no_instance_id(self):
        node = Node.objects.create(instance_id="", hostname="web-10")
        result = self.model_admin.latest_preflight(node)
        self.assertEqual(result, "No preflight recorded.")

    def test_latest_preflight_no_matching_run(self):
        node = Node.objects.create(instance_id="web-11", hostname="web-11")
        result = self.model_admin.latest_preflight(node)
        self.assertEqual(result, "No preflight recorded.")


class NodeChangePageRenderTests(TestCase):
    """The Node change page actually renders the sparkline + pipeline links."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="ops", email="ops@example.com", password="pw"
        )
        self.client.force_login(self.user)

    def test_node_change_page_renders_sparkline_and_pipeline_link(self):
        from django.urls import reverse

        from apps.checkers.models import CheckRun
        from apps.orchestration.models import PipelineRun

        node = Node.objects.create(instance_id="web-20", hostname="web-20")
        CheckRun.objects.create(
            checker_name="disk",
            hostname="web-20",
            status="ok",
            metrics={"worst_percent": 42.0},
            executed_at=timezone.now(),
        )
        run = PipelineRun.objects.create(trace_id="t", run_id="run-render", node=node)
        url = reverse("admin:alerts_node_change", args=[node.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<svg")
        self.assertContains(resp, f"/admin/orchestration/pipelinerun/{run.pk}/change/")


class NodeIncidentsColumnTests(TestCase):
    """The node changelist must say whether each machine is in trouble."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_superuser("nodeadmin", "node@test.com", "password")

    def setUp(self):
        self.client.login(username="nodeadmin", password="password")

    def _admin(self):
        return admin.site._registry[Node]

    def _node(self, instance_id="node-a", hostname="web-01"):
        return Node.objects.create(instance_id=instance_id, hostname=hostname)

    def _incident(self, node, *, severity="critical", status="open", alerts=1, title="cpu"):
        incident = Incident.objects.create(title=title, severity=severity, status=status)
        for i in range(alerts):
            Alert.objects.create(
                incident=incident,
                node=node,
                name="cpu",
                fingerprint=f"{node.instance_id}-{incident.id}-{i}",
                source="cluster",
                severity=severity,
                started_at=timezone.now(),
            )
        return incident

    def _annotated(self, node):
        """Refetch the node through the changelist queryset, so annotations exist."""
        request = RequestFactory().get("/admin/alerts/node/")
        request.user = self.user
        return self._admin().get_queryset(request).get(pk=node.pk)

    def test_column_is_in_list_display(self):
        self.assertIn("incidents", self._admin().list_display)

    def test_quiet_node_renders_a_dash(self):
        node = self._node()
        self.assertEqual(self._admin().incidents(self._annotated(node)), "—")

    def test_counts_split_by_severity_worst_first(self):
        node = self._node()
        self._incident(node, severity="warning", title="mem")
        self._incident(node, severity="critical", title="cpu")
        self._incident(node, severity="critical", title="disk")
        html = str(self._admin().incidents(self._annotated(node)))
        self.assertLess(html.index("2 CRITICAL"), html.index("1 WARNING"))

    def test_many_alerts_on_one_incident_count_once(self):
        node = self._node()
        self._incident(node, severity="critical", alerts=6)
        self.assertIn("1 CRITICAL", str(self._admin().incidents(self._annotated(node))))

    def test_acknowledged_counts_and_resolved_and_closed_do_not(self):
        node = self._node()
        self._incident(node, severity="critical", status="acknowledged", title="ack")
        self._incident(node, severity="critical", status="resolved", title="res")
        self._incident(node, severity="critical", status="closed", title="clo")
        html = str(self._admin().incidents(self._annotated(node)))
        self.assertIn("1 CRITICAL", html)

    def test_info_severity_is_counted(self):
        node = self._node()
        self._incident(node, severity="info")
        self.assertIn("1 INFO", str(self._admin().incidents(self._annotated(node))))

    def test_another_nodes_incidents_do_not_leak(self):
        node = self._node()
        other = self._node(instance_id="node-b", hostname="web-02")
        self._incident(other, severity="critical")
        self.assertEqual(self._admin().incidents(self._annotated(node)), "—")
        self.assertIn("1 CRITICAL", str(self._admin().incidents(self._annotated(other))))

    def test_link_filters_the_incident_changelist_to_this_node(self):
        node = self._node()
        self._incident(node, severity="critical")
        html = str(self._admin().incidents(self._annotated(node)))
        self.assertIn(f"alerts__node__id__exact={node.pk}", html)
        self.assertIn("severity__exact=critical", html)
        self.assertIn("status__in=open,acknowledged", html)

    def test_the_link_target_actually_filters(self):
        node = self._node()
        other = self._node(instance_id="node-b", hostname="web-02")
        self._incident(node, severity="critical", title="mine")
        self._incident(other, severity="critical", title="theirs")
        response = self.client.get(
            "/admin/alerts/incident/",
            {
                "alerts__node__id__exact": node.pk,
                "status__in": "open,acknowledged",
                "severity__exact": "critical",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("mine", body)
        self.assertNotIn("theirs", body)

    def test_column_is_sortable_by_total(self):
        quiet = self._node()
        busy = self._node(instance_id="node-b", hostname="web-02")
        self._incident(busy, severity="warning")
        request = RequestFactory().get("/admin/alerts/node/")
        request.user = self.user
        ordering = self._admin().incidents.admin_order_field
        ordered = list(self._admin().get_queryset(request).order_by(f"-{ordering}"))
        self.assertEqual([n.pk for n in ordered], [busy.pk, quiet.pk])

    def test_changelist_renders_the_column(self):
        node = self._node()
        self._incident(node, severity="critical")
        body = self.client.get("/admin/alerts/node/").content.decode()
        self.assertIn("1 CRITICAL", body)

    def test_counts_cost_no_extra_queries_per_node(self):
        for i in range(3):
            node = self._node(instance_id=f"node-{i}", hostname=f"web-{i}")
            self._incident(node, severity="critical")
        request = RequestFactory().get("/admin/alerts/node/")
        request.user = self.user
        with self.assertNumQueries(1):
            rendered = [self._admin().incidents(n) for n in self._admin().get_queryset(request)]
        self.assertEqual(len(rendered), 3)
