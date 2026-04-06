"""Tests for the unified preflight management command."""

import json
import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class PreflightCommandTests(TestCase):
    def _call(self, *args, **kwargs):
        out = StringIO()
        err = StringIO()
        call_command("preflight", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_human_output_has_dashboard_and_checks(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("System", output)
        self.assertIn("Role:", output)
        self.assertIn("Checks", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_output_valid(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("profile", data)
        self.assertIn("checks", data)
        self.assertIn("summary", data)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.management.commands.preflight.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_logger_called(self, mock_log, mock_read):
        mock_read.return_value = None
        self._call()
        mock_log.assert_called_once()

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_line(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("passed", output)
        self.assertIn("warning(s)", output)
        self.assertIn("error(s)", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_definitions_shown(self, mock_log, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call()
        self.assertIn("test-pipe", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        INSTANCE_ID="node-1",
        WEBHOOK_SECRET_CLUSTER="secret",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_agent_role_in_dashboard(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("agent", output)
        self.assertIn("hub.example.com", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(HUB_URL="", CLUSTER_ENABLED=True, WEBHOOK_SECRET_CLUSTER="secret")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_hub_role_in_dashboard(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("hub", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(
        HUB_URL="https://hub.example.com", CLUSTER_ENABLED=True, WEBHOOK_SECRET_CLUSTER="secret"
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_conflict_role_in_dashboard(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("CONFLICT", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_inactive_definition_shown_dimmed(self, mock_log, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="old-pipe",
            config={"nodes": []},
            is_active=False,
        )
        output, _ = self._call()
        self.assertIn("old-pipe", output)
        self.assertIn("inactive", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_check_levels_rendered(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        # Should contain at least OK and WARN (from installation checks in dev)
        self.assertIn("OK", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_error_summary_styling(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("error(s)", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    @patch("apps.checkers.preflight.checks._is_writable", return_value=True)
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_warnings_summary_styling(self, mock_log, mock_writable, mock_exists, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\nMISSING=x\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        PipelineDefinition.objects.create(name="p", config={}, is_active=True)
        output, _ = self._call()
        self.assertIn("warning(s)", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.checks._path_exists", return_value=True)
    @patch("apps.checkers.preflight.checks._is_writable", return_value=True)
    @patch("apps.checkers.preflight.checks.run_checks", return_value=[])
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(SECRET_KEY="a" * 50)
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_clean_summary_styling(
        self, mock_log, mock_run_checks, mock_writable, mock_exists, mock_read
    ):
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
        # Mock stat so .env permissions check doesn't see real file as world-readable
        mock_stat = patch(
            "pathlib.Path.stat", return_value=os.stat_result((0o600, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        )
        with mock_stat:
            output, _ = self._call()
        self.assertIn("0 warning(s)", output)
        self.assertIn("0 error(s)", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_includes_definitions(self, mock_log, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("definitions", data)
        self.assertTrue(len(data["definitions"]) > 0)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(INSTANCE_ID="node-1")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_instance_id_shown(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("Instance ID:", output)
        self.assertIn("node-1", output)
