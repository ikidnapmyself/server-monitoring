import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.drivers.base import ParsedAlert
from apps.alerts.forms import ADD_SECTION_FIELD, NodePolicyForm
from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.alerts.node_policy import FIELD_SPECS
from apps.alerts.reevaluation import reevaluate_severity
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

    def test_the_registry_is_readonly_and_config_is_not_a_raw_field(self):
        model_admin = self._admin()
        # Ingest-owned registry fields stay read-only...
        for registry_field in ["instance_id", "hostname", "last_source", "labels"]:
            self.assertIn(registry_field, model_admin.readonly_fields)
        # ...and config is off the field list entirely: NodePolicyForm owns it,
        # and a raw JSON widget beside the policy boxes would be a second writer
        # for the same column.
        self.assertNotIn("config", model_admin.fields)
        self.assertIs(model_admin.form, NodePolicyForm)

    def test_no_json_editor_widget_is_left_behind(self):
        # labels is a JSONField too, but it is read-only, so the override had
        # nothing left to apply to once config left the form.
        self.assertNotIn(db_models.JSONField, self._admin().formfield_overrides)


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
        self.assertContains(response, 'id="node_form"')

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


class NodePolicyFormWiringTests(TestCase):
    """The change page edits policy as labelled boxes, never as raw JSON.

    ``apps.alerts.reevaluation`` is fail-open, so a policy the admin lets
    through unvalidated does nothing and says nothing. These tests are about
    the page being the strict half of that bargain.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.user)

    def _url(self, node):
        return reverse("admin:alerts_node_change", args=[node.pk])

    def _node_reporting_cpu(self, **kwargs):
        """A peer whose sections come from the checkers its alerts name."""
        kwargs.setdefault("instance_id", "web-03")
        kwargs.setdefault("hostname", "web-03")
        node = Node.objects.create(**kwargs)
        Alert.objects.create(
            fingerprint="check:web-03:cpu",
            source="cluster",
            name="cpu high",
            severity="warning",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
        )
        return node

    def test_the_page_renders_the_policy_boxes_for_the_nodes_sections(self):
        node = self._node_reporting_cpu()
        response = self.client.get(self._url(node))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="policy__cpu__warning_threshold"')
        self.assertContains(response, 'name="policy__cpu__critical_threshold"')
        # and the operator text from the spec, so the box says what it does
        self.assertContains(response, "Critical at")

    def test_a_stored_policy_arrives_in_the_boxes(self):
        node = self._node_reporting_cpu(
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        )
        response = self.client.get(self._url(node))
        self.assertContains(response, 'value="80"')
        self.assertContains(response, 'value="95"')

    def test_the_raw_json_editor_is_gone(self):
        # Two writers for one column is a silent data-loss bug: whichever the
        # admin renders last wins. The JSON editor must not come back.
        node = self._node_reporting_cpu()
        response = self.client.get(self._url(node))
        self.assertNotContains(response, 'name="config"')
        self.assertNotContains(response, "jsoneditor")

    def test_an_inverted_pair_is_refused_and_writes_nothing(self):
        node = self._node_reporting_cpu(
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        )
        response = self.client.post(
            self._url(node),
            {
                "policy__cpu__warning_threshold": "90",
                "policy__cpu__critical_threshold": "10",
                "_continue": "1",
            },
        )
        self.assertEqual(response.status_code, 200)  # re-rendered, not redirected
        self.assertContains(response, "must not be below the warning")
        node.refresh_from_db()
        self.assertEqual(node.config, {"cpu": {"warning_threshold": 80, "critical_threshold": 95}})

    def test_a_valid_pair_is_written(self):
        node = self._node_reporting_cpu()
        response = self.client.post(
            self._url(node),
            {
                "policy__cpu__warning_threshold": "70",
                "policy__cpu__critical_threshold": "90",
            },
        )
        self.assertEqual(response.status_code, 302)
        node.refresh_from_db()
        self.assertEqual(
            node.config, {"cpu": {"warning_threshold": 70.0, "critical_threshold": 90.0}}
        )

    def test_the_overview_and_the_action_button_survive_the_form_swap(self):
        # PR #230's panels and the object action both hang off this page.
        node = self._node_reporting_cpu()
        response = self.client.get(self._url(node))
        self.assertContains(response, "Checker state")
        self.assertContains(response, "Re-evaluate open alerts")

    def test_without_an_object_there_are_no_policy_sections(self):
        # NodeAdmin forbids adding, but get_fieldsets is still called with
        # obj=None (ModelAdmin.get_fields does it), and sections_for would need
        # a saved row to read.
        request = RequestFactory().get("/admin/alerts/node/add/")
        request.user = self.user
        fieldsets = admin.site._registry[Node].get_fieldsets(request, None)
        self.assertEqual(len(fieldsets), 1)
        self.assertNotIn("config", fieldsets[0][1]["fields"])

    def test_the_registry_fields_still_show_read_only(self):
        node = self._node_reporting_cpu()
        response = self.client.get(self._url(node))
        self.assertContains(response, "web-03")
        self.assertNotContains(response, 'name="instance_id"')


class NodePolicyViewOnlyTests(TestCase):
    """A staff user who may look but not change must still get a page."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "looker", "look@example.com", "pw", is_staff=True
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_node", content_type__app_label="alerts")
        )
        self.client.force_login(self.user)

    def _node_with_a_cpu_policy(self):
        return Node.objects.create(
            instance_id="web-03",
            hostname="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )

    def test_the_change_page_renders_without_change_permission(self):
        # get_form folds the field list into ``exclude`` on this path, so a
        # None field list would raise there rather than render.
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        response = self.client.get(reverse("admin:alerts_node_change", args=[node.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "web-03")

    def test_a_set_policy_is_readable(self):
        # From the panel, which is the whole read-only answer for this reader.
        node = self._node_with_a_cpu_policy()
        response = self.client.get(reverse("admin:alerts_node_change", args=[node.pk]))
        self.assertContains(response, "Warning at")
        self.assertContains(response, "80")

    def test_a_set_policy_is_never_shown_as_none(self):
        # The admin renders every fieldset field read-only for this reader, and
        # AdminReadonlyField cannot read a value for a field the form added in
        # __init__, so the boxes came out as "None" directly under a panel
        # saying "Warning at 80". A page reporting a live policy as None is the
        # misreading this whole form exists to kill.
        node = self._node_with_a_cpu_policy()
        response = self.client.get(reverse("admin:alerts_node_change", args=[node.pk]))
        self.assertNotContains(response, "field-policy__cpu__warning_threshold")
        self.assertNotContains(response, "field-policy__cpu__critical_threshold")
        self.assertNotContains(response, "cpu policy")

    def test_the_add_section_select_is_not_offered(self):
        # It could only ever render as "Add a policy for: None".
        node = self._node_with_a_cpu_policy()
        response = self.client.get(reverse("admin:alerts_node_change", args=[node.pk]))
        self.assertNotContains(response, ADD_SECTION_FIELD)
        self.assertNotContains(response, "Add a policy section")

    def test_a_reader_who_may_change_still_gets_the_boxes(self):
        # The other side of the branch: the fieldsets are dropped for lack of
        # change permission, not for every reader.
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_node", content_type__app_label="alerts")
        )
        node = self._node_with_a_cpu_policy()
        response = self.client.get(reverse("admin:alerts_node_change", args=[node.pk]))
        self.assertContains(response, 'name="policy__cpu__warning_threshold"')


class NodeAddPolicySectionTests(TestCase):
    """Readying a policy for a checker the node has not reported yet.

    The mechanism is an empty policy dict: ``to_config`` keeps an emptied
    checker's key and ``sections_for`` counts a configured checker, so writing
    ``{"disk": {}}`` is what makes the disk boxes appear. It has to be inert at
    runtime, and that is the last test here.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.user)
        self.node = Node.objects.create(instance_id="web-03", hostname="web-03")

    def _url(self):
        return reverse("admin:alerts_node_change", args=[self.node.pk])

    def _cpu_alert(self, severity="critical"):
        return ParsedAlert(
            fingerprint="check:web-03:cpu",
            name="cpu high",
            status="firing",
            started_at=timezone.now(),
            severity=severity,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": '{"cpu_percent": 95.2}'},
        )

    def test_the_select_renders_on_the_change_page(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="{ADD_SECTION_FIELD}"')
        self.assertContains(response, 'value="disk"')

    def test_choosing_a_checker_makes_its_fieldset_appear(self):
        response = self.client.post(self._url(), {ADD_SECTION_FIELD: "disk"})
        self.assertEqual(response.status_code, 302)
        self.node.refresh_from_db()
        self.assertEqual(self.node.config, {"disk": {}})
        page = self.client.get(self._url())
        self.assertContains(page, 'name="policy__disk__warning_threshold"')
        self.assertContains(page, "disk policy")
        # and it is no longer on offer in the select
        self.assertNotContains(page, 'value="disk"')

    def test_the_select_is_gone_once_every_checker_has_a_section(self):
        self.node.config = {checker: {} for checker in FIELD_SPECS}
        self.node.save()
        response = self.client.get(self._url())
        self.assertNotContains(response, f'name="{ADD_SECTION_FIELD}"')

    def test_the_select_sits_after_the_policy_sections(self):
        self.node.config = {"cpu": {}}
        self.node.save()
        request = RequestFactory().get(self._url())
        request.user = self.user
        fieldsets = admin.site._registry[Node].get_fieldsets(request, self.node)
        self.assertEqual(fieldsets[-1][1]["fields"], [ADD_SECTION_FIELD])
        self.assertIn("cpu policy", [name for name, _opts in fieldsets])
        self.assertNotIn(ADD_SECTION_FIELD, fieldsets[0][1]["fields"])

    def test_a_checker_outside_the_specs_writes_nothing(self):
        response = self.client.post(self._url(), {ADD_SECTION_FIELD: "made_up"})
        self.assertEqual(response.status_code, 200)  # re-rendered, not redirected
        self.node.refresh_from_db()
        self.assertEqual(self.node.config, {})

    def test_an_empty_policy_does_not_change_how_alerts_score(self):
        # The claim the whole mechanism rests on, against the real scorer:
        # _reevaluate returns early on a falsy config entry, so {"cpu": {}}
        # readies the section and scores exactly like no policy at all.
        before = reevaluate_severity(self._cpu_alert())
        self.client.post(self._url(), {ADD_SECTION_FIELD: "cpu"})
        self.node.refresh_from_db()
        self.assertEqual(self.node.config, {"cpu": {}})
        after = reevaluate_severity(self._cpu_alert())
        self.assertEqual(after.severity, before.severity)
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.severity, "critical")
        self.assertNotIn("severity_reevaluated", after.annotations)
        self.assertIsNone(after.ended_at)


class NodePolicySaveRedirectTests(TestCase):
    """A save that changes scoring lands on the re-evaluate preview.

    Saving a threshold does nothing to the alerts already open, and today the
    operator has to remember a second button for that. The redirect is still
    two deliberate acts: the preview shows what would change and waits for a
    confirm, because an admin save that silently resolves incidents is worse
    than the button nobody presses.
    """

    ACTION_URL_NAME = "admin:alerts_node_actions"

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("ops", "ops@example.com", "pw")
        self.client.force_login(self.user)
        self.node = Node.objects.create(
            instance_id="web-03",
            hostname="web-03",
            config={"cpu": {"warning_threshold": 80, "critical_threshold": 95}},
        )
        Alert.objects.create(
            fingerprint="check:web-03:cpu",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=self.node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 42.0})},
        )

    def _url(self):
        return reverse("admin:alerts_node_change", args=[self.node.pk])

    def _preview_url(self):
        return reverse(
            self.ACTION_URL_NAME,
            kwargs={"pk": self.node.pk, "tool": "reevaluate_open_alerts"},
        )

    def _changelist_url(self):
        return reverse("admin:alerts_node_changelist")

    def test_a_changed_threshold_redirects_to_the_preview(self):
        # 50/60 against the alert's 42%: the open critical would become resolved,
        # so the preview has something to show.
        response = self.client.post(
            self._url(),
            {
                "policy__cpu__warning_threshold": "50",
                "policy__cpu__critical_threshold": "60",
            },
        )
        self.assertRedirects(response, self._preview_url(), target_status_code=200)

    def test_the_redirect_target_is_the_real_preview_page(self):
        response = self.client.post(
            self._url(),
            {
                "policy__cpu__warning_threshold": "50",
                "policy__cpu__critical_threshold": "60",
            },
            follow=True,
        )
        self.assertContains(response, "Confirm re-evaluation")
        self.assertContains(response, "cpu")
        # The preview is a preview: nothing is applied by the save.
        self.assertEqual(Alert.objects.get().status, "firing")

    def test_an_untouched_save_goes_back_to_the_changelist(self):
        response = self.client.post(
            self._url(),
            {
                "policy__cpu__warning_threshold": "80",
                "policy__cpu__critical_threshold": "95",
            },
        )
        self.assertRedirects(response, self._changelist_url())
        self.node.refresh_from_db()
        # and the stored ints are still ints, which is what made it compare equal
        self.assertEqual(self.node.config["cpu"]["warning_threshold"], 80)

    def test_opening_a_section_goes_back_to_the_changelist(self):
        response = self.client.post(
            self._url(),
            {
                "policy__cpu__warning_threshold": "80",
                "policy__cpu__critical_threshold": "95",
                ADD_SECTION_FIELD: "memory",
            },
        )
        self.assertRedirects(response, self._changelist_url())
        self.node.refresh_from_db()
        self.assertEqual(self.node.config["memory"], {})

    def test_save_and_continue_editing_still_stays_on_the_form(self):
        response = self.client.post(
            self._url(),
            {
                "policy__cpu__warning_threshold": "10",
                "policy__cpu__critical_threshold": "20",
                "_continue": "1",
            },
        )
        self.assertRedirects(response, self._url())

    def test_the_reevaluate_button_still_works_on_its_own(self):
        # No save involved: the action is still reachable and still previews.
        response = self.client.get(self._preview_url())
        self.assertContains(response, "Confirm re-evaluation")


class EffectivePolicyPanelTests(TestCase):
    """The read-only panel: what scores, and what nothing reads.

    It is rendered for everyone. An operator editing thresholds gets a plain
    statement of what is active today beside the boxes that will change it, and
    a staff user with view-but-not-change permission gets the only readable copy
    of the policy on the page: Django renders every fieldset field read-only for
    them, and AdminReadonlyField cannot resolve the form's dynamically-added
    non-model names, so each box falls back to an empty-value dash.
    """

    CONFIG = {
        "cpu": {"warning_threshold": 81, "critical_threshold": 96, "sample_window": 5},
        "made_up": {"anything": 1},
    }

    def setUp(self):
        self.node = Node.objects.create(
            instance_id="web-03", hostname="web-03", config=dict(self.CONFIG)
        )

    def _url(self):
        return reverse("admin:alerts_node_change", args=[self.node.pk])

    def _login(self, *codenames):
        user = get_user_model().objects.create_user(
            "viewer", "viewer@example.com", "pw", is_staff=True
        )
        for codename in codenames:
            user.user_permissions.add(
                Permission.objects.get(codename=codename, content_type__app_label="alerts")
            )
        self.client.force_login(user)
        return user

    def test_a_change_permitted_user_sees_the_policy_in_effect(self):
        self._login("view_node", "change_node")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Policy in effect")
        self.assertContains(response, "81")
        self.assertContains(response, "96")

    def test_a_view_only_user_sees_the_policy_values(self):
        # The gap this panel closes: without it the page is a column of dashes.
        self._login("view_node")
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Policy in effect")
        self.assertContains(response, "81")
        self.assertContains(response, "96")

    def test_the_keys_nothing_reads_are_named_with_their_note(self):
        self._login("view_node")
        response = self.client.get(self._url())
        self.assertContains(response, "Not honoured")
        self.assertContains(response, "sample_window")
        self.assertContains(response, "made_up")
        self.assertContains(response, "Nothing reads")

    def test_the_overview_panels_and_the_action_button_still_render(self):
        self._login("view_node", "change_node")
        response = self.client.get(self._url())
        self.assertContains(response, "Checker state")
        self.assertContains(response, "Recent pipeline runs")
        self.assertContains(response, "Re-evaluate open alerts")

    def test_a_half_filled_pair_is_shown_as_not_scoring(self):
        # It cannot come from the form (clean_thresholds refuses it), but a
        # hand-written config can, and it is exactly the silently-dead policy an
        # operator needs to find.
        self.node.config = {"memory": {"warning_threshold": 70}}
        self.node.save()
        self._login("view_node")
        response = self.client.get(self._url())
        self.assertContains(response, "Saved but not scoring")
        self.assertContains(response, "Set a critical threshold too, or clear both.")
        self.assertContains(response, "70")

    def test_a_complete_pair_is_not_called_out_as_not_scoring(self):
        self._login("view_node")
        response = self.client.get(self._url())
        self.assertContains(response, "Policy in effect")
        self.assertNotContains(response, "Saved but not scoring")

    def test_a_node_with_no_policy_says_so(self):
        self.node.config = {}
        self.node.save()
        self._login("view_node")
        response = self.client.get(self._url())
        self.assertContains(response, "No hub-side policy")
