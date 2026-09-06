import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.orchestration.models import PipelineRun


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

    def test_the_help_warns_that_applying_notifies(self):
        from apps.alerts.management.commands.reevaluate_node_alerts import Command

        self.assertIn("notifies", Command.help)
        self.assertIn("--dry-run", Command.help)

    def test_the_preview_prints_the_skips_with_their_reasons(self):
        node = self._node({})
        self._firing_cpu_alert(node)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=out)
        self.assertIn("No policy set for cpu on web-03.", out.getvalue())

    def test_the_preview_prints_how_many_runs_applying_would_create(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        incident = Incident.objects.create(title="t", severity="critical", status="open")
        alert = self._firing_cpu_alert(node)
        alert.incident = incident
        alert.save()
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=out)
        self.assertIn("Applying will create 1 pipeline run(s) and notify on them.", out.getvalue())

    def test_the_preview_rounds_the_value(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        self._firing_cpu_alert(node, value=41.199999999999996)
        out = StringIO()
        call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=out)
        self.assertIn("(41.2)", out.getvalue())

    def test_dry_run_writes_nothing_and_enqueues_nothing(self):
        node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
        incident = Incident.objects.create(title="t", severity="critical", status="open")
        alert = self._firing_cpu_alert(node)
        alert.incident = incident
        alert.save()
        call_command("reevaluate_node_alerts", "web-03", "--dry-run", stdout=StringIO())
        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertEqual(PipelineRun.objects.count(), 0)

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

    @patch("builtins.input", side_effect=EOFError)
    def test_prompt_eof_aborts(self, _mock_input):
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
