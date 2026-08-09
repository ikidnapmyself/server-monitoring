import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.db import models as db_models
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django_json_widget.widgets import JSONEditorWidget

from apps.alerts.models import Alert, Node


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
