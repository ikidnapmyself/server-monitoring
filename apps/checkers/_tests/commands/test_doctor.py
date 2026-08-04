import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.alerts.models import Node
from config.models import APIKey


@override_settings(API_KEY_AUTH_ENABLED=True)
class DoctorCommandTests(TestCase):
    def test_json_reports_nodes_and_ingest_readiness(self):
        Node.objects.create(instance_id="web-03", hostname="h")
        APIKey.objects.create(name="agent-web-03")  # active by default

        out = StringIO()
        call_command("doctor", "--json", stdout=out)
        data = json.loads(out.getvalue())

        self.assertIn("cluster", data)
        self.assertTrue(data["cluster"]["accepting_pushes"])  # >=1 active key
        self.assertEqual(data["cluster"]["active_api_keys"], 1)
        self.assertEqual(data["cluster"]["known_nodes"], 1)

    def test_no_keys_means_not_accepting(self):
        out = StringIO()
        call_command("doctor", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertFalse(data["cluster"]["accepting_pushes"])
        self.assertEqual(data["cluster"]["active_api_keys"], 0)

    def test_human_output_runs_without_json(self):
        Node.objects.create(instance_id="web-04", hostname="h")
        APIKey.objects.create(name="agent-web-04")

        out = StringIO()
        call_command("doctor", stdout=out)
        text = out.getvalue()

        self.assertIn("Doctor", text)
        self.assertIn("Accepting pushes:", text)
        self.assertIn("Known nodes:", text)
        self.assertIn("Inbox:", text)

    def test_json_reports_inbox_depth(self):
        from apps.orchestration.orchestrator import PipelineOrchestrator

        for _ in range(3):
            PipelineOrchestrator().start_pipeline(payload={}, source="x")

        out = StringIO()
        call_command("doctor", "--json", stdout=out)
        data = json.loads(out.getvalue())

        self.assertEqual(data["inbox"]["pending"], 3)
        self.assertEqual(data["inbox"]["processing"], 0)
        self.assertFalse(data["inbox"]["over_threshold"])

    @override_settings(INBOX_DEPTH_WARN=1)
    def test_backlog_over_threshold_emits_warning(self):
        from apps.orchestration.orchestrator import PipelineOrchestrator

        for _ in range(2):
            PipelineOrchestrator().start_pipeline(payload={}, source="x")

        out = StringIO()
        call_command("doctor", "--json", stdout=out)
        data = json.loads(out.getvalue())

        self.assertTrue(data["inbox"]["over_threshold"])
        self.assertTrue(
            any(c["level"] == "WARNING" and "Inbox backlog" in c["message"] for c in data["checks"])
        )
