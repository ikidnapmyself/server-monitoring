import json
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.checkers.checkers.base import CheckResult, CheckStatus


class PushToHubTests(TestCase):
    """Tests for push_to_hub management command."""

    @override_settings(HUB_URL="")
    def test_fails_without_hub_url(self):
        """Command exits with error when HUB_URL is not configured."""
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=out)
        self.assertIn("HUB_URL", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_dry_run_does_not_post(self, mock_registry):
        """--dry-run shows payload but doesn't POST."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="CPU OK",
            metrics={"cpu_percent": 10.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", stdout=out)
        output = out.getvalue()
        self.assertIn("dry run", output.lower())

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_posts_to_hub_url(self, mock_urlopen, mock_registry):
        """Command POSTs checker results to HUB_URL."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.WARNING,
            message="CPU at 75%",
            metrics={"cpu_percent": 75.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 202
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", stdout=out)

        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["source"], "cluster")
        self.assertTrue(len(payload["alerts"]) > 0)

    @override_settings(HUB_URL="https://hub.example.com", INSTANCE_ID="test-agent")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_uses_instance_id_from_settings(self, mock_urlopen, mock_registry):
        """Command uses INSTANCE_ID from settings."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="OK",
            metrics={},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 202
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", stdout=out)

        request = mock_urlopen.call_args[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload["instance_id"], "test-agent")

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_json_output(self, mock_registry):
        """--json outputs JSON format."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="OK",
            metrics={},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", "--json", stdout=out)
        output = out.getvalue()
        parsed = json.loads(output)
        self.assertIn("alerts", parsed)
        self.assertEqual(parsed["source"], "cluster")
