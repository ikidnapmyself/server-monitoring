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
        PipelineDefinition.objects.create(name="test-pipe", is_active=True)
        output, _ = self._call()
        self.assertIn("test-pipe", output)
        # A lane with no channel says so rather than printing a bare "None".
        self.assertIn("channel: no channel", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_definition_channel_name_shown(self, mock_log, mock_read):
        """The lane's channel name reaches the rendered dashboard, not just the dict."""
        from apps.notify.models import NotificationChannel

        mock_read.return_value = None
        ch = NotificationChannel.objects.create(
            name="ops-slack", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        PipelineDefinition.objects.create(name="wired-pipe", is_active=True, channel=ch)
        output, _ = self._call()
        self.assertIn("channel: ops-slack", output)
        self.assertNotIn("routes nowhere", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_definition_inactive_channel_marked_as_routing_nowhere(self, mock_log, mock_read):
        """An active lane wired to a dead channel must not read as routed."""
        from apps.notify.models import NotificationChannel

        mock_read.return_value = None
        ch = NotificationChannel.objects.create(
            name="dead-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
            is_active=False,
        )
        PipelineDefinition.objects.create(name="stale-pipe", is_active=True, channel=ch)
        output, _ = self._call()
        # Assert the two facts, not the sentence: the operator must see WHICH channel
        # is wired (so they can fix it) and that it does not route. Pinning the exact
        # prose would make a wording change a test failure.
        self.assertIn("dead-slack", output)
        self.assertIn("routes nowhere", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(
        HUB_URL="https://hub.example.com",
        API_KEY_AUTH_ENABLED=False,
        INSTANCE_ID="node-1",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_agent_role_in_dashboard(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("agent", output.lower())
        self.assertIn("hub.example.com", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(HUB_URL="", API_KEY_AUTH_ENABLED=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_hub_role_in_dashboard(self, mock_log, mock_read):
        from config.models import APIKey

        APIKey.objects.create(name="agent-x")  # active → node is receiving
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("hub", output.lower())

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @override_settings(HUB_URL="https://hub.example.com", API_KEY_AUTH_ENABLED=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_agent_and_hub_role_in_dashboard(self, mock_log, mock_read):
        from config.models import APIKey

        APIKey.objects.create(name="agent-x")
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("agent+hub", output.lower())  # valid, not a conflict

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_inactive_definition_shown_dimmed(self, mock_log, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(name="old-pipe", is_active=False)
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

    @patch("apps.checkers.preflight.checks._read_file", return_value=None)
    @patch("apps.checkers.preflight.checks._path_exists", return_value=False)
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_error_summary_styling(self, mock_log, mock_path_exists, mock_read):
        # A missing .env (path_exists=False) yields an error, exercising the
        # error-summary styling — no longer relying on the removed cluster conflict.
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
        PipelineDefinition.objects.create(name="p", is_active=True)
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
        channel = NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        PipelineDefinition.objects.create(name="p", is_active=True)
        # The seeded lanes list NOTIFY; a clean summary means delivery works too,
        # so bind them the way a real deployment is bound.
        PipelineDefinition.objects.update(channel=channel)
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
        PipelineDefinition.objects.create(name="test-pipe", is_active=True)
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
