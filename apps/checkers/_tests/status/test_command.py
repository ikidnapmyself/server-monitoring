"""Tests for the system_status management command."""

import json
import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition, PipelineRun


class SystemStatusCommandTests(TestCase):
    def _call(self, *args, **kwargs):
        out = StringIO()
        err = StringIO()
        call_command("system_status", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_human_output_contains_profile(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("System Profile", output)
        self.assertIn("Role:", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_output_is_valid(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("profile", data)
        self.assertIn("pipeline", data)
        self.assertIn("definitions", data)
        self.assertIn("checks", data)
        self.assertIn("summary", data)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_checks_only_skips_dashboard(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--checks-only")
        self.assertNotIn("System Profile", output)
        self.assertIn("Consistency", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_verbose_shows_ok_checks(self, mock_read):
        mock_read.return_value = None
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        output, _ = self._call("--verbose")
        self.assertIn("OK", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_pipeline_definitions_shown(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call()
        self.assertIn("Pipeline Definitions", output)
        self.assertIn("test-pipe", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_definitions_include_stages(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertTrue(len(data["definitions"]) > 0)
        self.assertIn("stages", data["definitions"][0])

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_counts(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        summary = data["summary"]
        self.assertIn("passed", summary)
        self.assertIn("warnings", summary)
        self.assertIn("errors", summary)

    @patch("apps.checkers.status.env_checks._read_file")
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        INSTANCE_ID="node-1",
        WEBHOOK_SECRET_CLUSTER="secret",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_renders_agent_role_with_instance_id(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("agent", output)
        self.assertIn("hub.example.com", output)
        self.assertIn("Instance ID:", output)
        self.assertIn("node-1", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @override_settings(HUB_URL="", CLUSTER_ENABLED=True, WEBHOOK_SECRET_CLUSTER="secret")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_renders_hub_role(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("hub (accepting cluster payloads)", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_renders_conflict_role(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("CONFLICT", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_renders_intelligence_and_last_run(self, mock_read):
        mock_read.return_value = None
        IntelligenceProvider.objects.create(name="ai", provider="claude", is_active=True)
        PipelineRun.objects.create(trace_id="t1", run_id="r1", status="notified")
        output, _ = self._call()
        self.assertIn("ai (claude)", output)
        self.assertIn("notified", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_renders_inactive_definition(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="old-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "email"}}]},
            is_active=False,
        )
        output, _ = self._call()
        self.assertIn("old-pipe", output)
        self.assertIn("inactive", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_all_checks_passed_message(self, mock_read):
        """When all checks return ok, non-verbose mode shows 'All checks passed'."""

        def side_effect(path):
            if path.name == ".env":
                return "DJANGO_SECRET_KEY=test\n"
            if path.name == ".env.sample":
                return "DJANGO_SECRET_KEY=\n"
            if path.name == "settings.py":
                return 'os.environ.get("DJANGO_SECRET_KEY")\n'
            return None

        mock_read.side_effect = side_effect
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        PipelineDefinition.objects.create(name="p", config={}, is_active=True)
        output, _ = self._call()
        self.assertIn("All checks passed", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_with_errors(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("error(s)", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_warnings_only(self, mock_read):
        """Summary with warnings but no errors uses warning style."""
        mock_read.return_value = None  # triggers .env not found error? No — returns None for .env

        # .env missing = error. We need warnings only.
        # Easiest: have .env + .env.sample with drift (warn), no cluster/runtime errors.
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\nMISSING=val\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        PipelineDefinition.objects.create(name="p", config={}, is_active=True)
        output, _ = self._call()
        # Should have warnings but no errors from cluster/runtime
        self.assertIn("warning(s)", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
    )
    def test_summary_clean_no_errors_no_warnings(self, mock_read):
        """When all checks pass, summary line uses success style."""

        def side_effect(path):
            if path.name == ".env":
                return "DJANGO_SECRET_KEY=test\n"
            if path.name == ".env.sample":
                return "DJANGO_SECRET_KEY=\n"
            if path.name == "settings.py":
                return 'os.environ.get("DJANGO_SECRET_KEY")\n'
            return None

        mock_read.side_effect = side_effect
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        PipelineDefinition.objects.create(name="p", config={}, is_active=True)
        output, _ = self._call()
        # Summary should contain "0 error(s)" and "0 warning(s)"
        self.assertIn("0 warning(s)", output)
        self.assertIn("0 error(s)", output)
