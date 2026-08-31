import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django_json_widget.widgets import JSONEditorWidget

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.checkers.models import CheckRun, PreflightRun
from apps.orchestration.models import PipelineRun


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


class NodeChangeFormTests(TestCase):
    """The change page renders the operator overview above the registry form."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.user)

    def _url(self, node):
        return reverse("admin:alerts_node_change", args=[node.pk])

    def test_the_overview_renders_above_the_form(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(self._url(node))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peer")
        self.assertContains(response, "Checker state")
        # and the normal admin form is still there
        self.assertContains(response, 'name="config"')

    def test_the_hubs_own_page_shows_its_preflight(self):
        # The regression: this said "No preflight recorded" for the hub itself.
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        PreflightRun.objects.create(instance_id=local_instance_id(), overall_status="ok", passed=11)
        response = self.client.get(self._url(node))
        self.assertContains(response, "This hub")
        self.assertNotContains(response, "No preflight recorded")

    def test_the_reevaluate_action_button_is_on_the_page(self):
        # django_object_actions fills object-tools-items with the change_actions
        # buttons. A change_form_template that skips its template silently drops
        # them: the URL stays registered, the button vanishes.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(self._url(node))
        self.assertContains(response, "Re-evaluate open alerts")

    def test_a_peer_is_told_why_it_has_no_charts(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(self._url(node))
        self.assertContains(response, "not pushed to a hub")

    def test_the_local_node_renders_its_charts_and_pipeline_runs(self):
        node = Node.objects.create(instance_id=local_instance_id(), hostname="hub")
        CheckRun.objects.create(
            checker_name="disk", hostname="hub", status="ok", metrics={"worst_percent": 42.0}
        )
        run = PipelineRun.objects.create(trace_id="t", run_id="run-render", node=node)
        response = self.client.get(self._url(node))
        self.assertContains(response, "<svg")
        self.assertContains(response, f"/admin/orchestration/pipelinerun/{run.pk}/change/")

    def test_no_object_means_no_overview(self):
        # NodeAdmin forbids adding, but render_change_form is still called with
        # obj=None on that path; the panels must be absent, not crash.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        node_admin = admin.site._registry[Node]
        context = dict(self.client.get(self._url(node)).context_data)
        context.pop("node_overview")
        request = RequestFactory().get("/admin/alerts/node/add/")
        request.user = self.user
        response = node_admin.render_change_form(request, context, add=True, obj=None)
        self.assertNotIn("node_overview", response.context_data)

    def test_a_hostile_hostname_is_escaped_but_the_chips_are_not(self):
        # A hostname arrives over a webhook, so it must be autoescaped. The chips
        # are SafeString from format_html and must still render as markup.
        node = Node.objects.create(instance_id="web-03", hostname="<script>alert(1)</script>")
        incident = Incident.objects.create(title="cpu high", severity="critical", status="open")
        Alert.objects.create(
            incident=incident,
            node=node,
            name="cpu",
            fingerprint="cpu-hostile",
            source="cluster",
            severity="critical",
            started_at=timezone.now(),
        )
        body = self.client.get(self._url(node)).content.decode()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("1 CRITICAL", body)  # chip markup survived unescaped

    def test_an_incident_title_from_a_webhook_is_escaped(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        incident = Incident.objects.create(
            title="<img src=x onerror=1>", severity="critical", status="open"
        )
        Alert.objects.create(
            incident=incident,
            node=node,
            name="cpu",
            fingerprint="cpu-hostile-title",
            source="cluster",
            severity="critical",
            started_at=timezone.now(),
        )
        body = self.client.get(self._url(node)).content.decode()
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;img src=x", body)


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
