"""The per-alert Re-evaluate action on AlertAdmin.

The operator complaint this closes: the re-evaluate button lived on the node, so
the answer to "why has this alert not budged?" was on a different page from the
alert. Every refusal here has to arrive as a sentence about this alert.
"""

import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.orchestration.models import PipelineOrigin, PipelineRun


class AlertReevaluateActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.model_admin = admin.site._registry[Alert]
        self.user = get_user_model().objects.create_superuser(
            username="ops", email="ops@example.com", password="pw"
        )

    def _request(self, method="get", data=None):
        request = getattr(self.factory, method)("/", data or {})
        request.user = self.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def _alert(self, **overrides):
        fields = {
            "fingerprint": "cpu-web-03",
            "source": "cluster",
            "name": "cpu high",
            "severity": "critical",
            "status": "firing",
            "started_at": timezone.now(),
            "labels": {"checker": "cpu", "instance_id": "web-03"},
            "annotations": {"metrics": json.dumps({"cpu_percent": 42.0})},
        }
        fields.update(overrides)
        return Alert.objects.create(**fields)

    def _rendered(self, response):
        self.assertIsInstance(response, TemplateResponse)
        response.render()
        return response.content.decode()

    def test_action_registered(self):
        self.assertIn("reevaluate", self.model_admin.change_actions)

    def test_the_reevaluate_button_is_on_the_alert_change_page(self):
        # django_object_actions fills object-tools-items with the change_actions
        # buttons; an admin that skips its change form template keeps the URL and
        # loses the button.
        alert = self._alert()
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:alerts_alert_change", args=[alert.pk]))
        self.assertContains(response, "Re-evaluate")

    def test_get_previews_without_writing(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._alert()
        content = self._rendered(self.model_admin.reevaluate(self._request("get"), alert))
        self.assertIn("cpu high", content)
        self.assertIn('value="Confirm"', content)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_post_with_confirm_applies(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._alert()
        request = self._request("post", {"confirm": "1"})
        self.assertIsNone(self.model_admin.reevaluate(request, alert))
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")
        messages = list(request._messages)
        self.assertEqual(len(messages), 1)
        self.assertIn("Resolved 1", messages[0].message)

    def test_post_with_confirm_and_no_changes_applies_nothing(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 1, "critical_threshold": 2}},
        )
        alert = self._alert()
        request = self._request("post", {"confirm": "1"})
        content = self._rendered(self.model_admin.reevaluate(request, alert))
        self.assertIn("Nothing here would change.", content)
        self.assertEqual(list(request._messages), [])
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")

    def test_staff_without_change_permission_is_denied(self):
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        alert = self._alert()
        staff = get_user_model().objects.create_user(
            username="viewer", email="viewer@example.com", password="pw", is_staff=True
        )
        request = self._request("post", {"confirm": "1"})
        request.user = staff
        with self.assertRaises(PermissionDenied):
            self.model_admin.reevaluate(request, alert)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_an_alert_whose_checker_has_no_scorer_says_so_and_offers_no_confirm(self):
        Node.objects.create(instance_id="web-03", config={})
        alert = self._alert(labels={"checker": "raid", "instance_id": "web-03"})
        content = self._rendered(self.model_admin.reevaluate(self._request("get"), alert))
        self.assertIn("What was left alone, and why", content)
        self.assertIn("raid is not re-evaluatable. No scorer knows it.", content)
        self.assertNotIn('value="Confirm"', content)

    def test_an_alert_whose_node_has_no_policy_for_its_checker_says_so(self):
        Node.objects.create(instance_id="web-03", config={"memory": {}})
        alert = self._alert()
        content = self._rendered(self.model_admin.reevaluate(self._request("get"), alert))
        self.assertIn("No policy set for cpu on web-03.", content)
        self.assertNotIn('value="Confirm"', content)

    def test_an_unregistered_node_is_named_in_the_intro_and_nothing_is_written(self):
        alert = self._alert()
        request = self._request("post", {"confirm": "1"})
        content = self._rendered(self.model_admin.reevaluate(request, alert))
        self.assertIn("names web-03, which is not registered on this hub", content)
        self.assertIn("No policy set for cpu on web-03.", content)
        self.assertNotIn('value="Confirm"', content)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_an_alert_with_no_instance_id_label_says_no_node_can_score_it(self):
        alert = self._alert(labels={"checker": "cpu"})
        content = self._rendered(self.model_admin.reevaluate(self._request("get"), alert))
        self.assertIn("carries no instance_id label", content)
        self.assertNotIn('value="Confirm"', content)

    def test_a_resolved_alert_that_still_breaches_warns_before_re_firing_it(self):
        """The re-open warning, reached through the real action.

        ``ReevalScope.for_alert`` does not filter on status, so this is the first
        surface where a resolved alert can be scored back to firing.
        """
        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 10, "critical_threshold": 20}},
        )
        incident = Incident.objects.create(title="cpu", severity="info", status="open")
        alert = self._alert(
            severity="info",
            status="resolved",
            ended_at=timezone.now(),
            incident=incident,
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )

        content = self._rendered(self.model_admin.reevaluate(self._request("get"), alert))
        self.assertIn("This will re-open 1 resolved alert(s).", content)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")

        request = self._request("post", {"confirm": "1"})
        self.assertIsNone(self.model_admin.reevaluate(request, alert))
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertIsNone(alert.ended_at)
        self.assertEqual(
            PipelineRun.objects.filter(incident=incident, origin=PipelineOrigin.MANUAL).count(), 1
        )

    def test_the_back_link_points_at_the_alert_not_the_node(self):
        node = Node.objects.create(instance_id="web-03", config={})
        alert = self._alert()
        response = self.model_admin.reevaluate(self._request("get"), alert)
        alert_url = reverse("admin:alerts_alert_change", args=[alert.pk])
        self.assertEqual(response.context_data["back_url"], alert_url)
        content = self._rendered(response)
        self.assertIn(f'<a href="{alert_url}" class="button cancel-link">Back</a>', content)
        # The node may still be linked from a skip row, which is a way forward,
        # not a way back: what must not happen is the page offering it as Back.
        node_url = reverse("admin:alerts_node_change", args=[node.pk])
        self.assertNotIn(f'<a href="{node_url}" class="button cancel-link">', content)
