import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Node


class ReevaluateNodeAlertsCommandTests(TestCase):
    def _node(self, cfg):
        return Node.objects.create(instance_id="web-03", config=cfg)

    def _firing_cpu_alert(self, node, value=42.0):
        return Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": value})},
        )

    def test_unknown_instance_id_errors(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("reevaluate_node_alerts", "nope", stdout=StringIO())
        self.assertIn("nope", str(ctx.exception))

    def test_dry_run_previews_without_writing(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=out)
        self.assertIn("cpu:", out.getvalue())
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    def test_no_changes_prints_and_stops(self):
        node = self._node({})
        self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", stdout=out)
        self.assertIn("No open alerts need re-evaluation.", out.getvalue())

    def test_noinput_applies(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", "--noinput", stdout=out)
        self.assertIn("Resolved 1", out.getvalue())
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")

    @patch("builtins.input", return_value="n")
    def test_prompt_no_aborts(self, _mock_input):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", stdout=out)
        self.assertIn("Aborted.", out.getvalue())
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")

    @patch("builtins.input", return_value="y")
    def test_prompt_yes_applies(self, _mock_input):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        alert = self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", stdout=out)
        self.assertIn("Resolved 1", out.getvalue())
        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")
