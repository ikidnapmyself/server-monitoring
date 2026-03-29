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

    @override_settings(HUB_URL="https://hub.example.com")
    def test_checkers_flag_filters(self):
        """--checkers flag runs only specified checkers."""
        cpu_cls = MagicMock()
        cpu_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mem_cls = MagicMock()
        mem_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="memory"
        )
        registry = {"cpu": cpu_cls, "memory": mem_cls}

        out = StringIO()
        with patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY", registry):
            call_command("push_to_hub", "--dry-run", "--checkers", "cpu", stdout=out)
        cpu_cls.assert_called_once()
        mem_cls.assert_not_called()

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_checker_exception_is_caught(self, mock_registry):
        """A failing checker should be skipped, not crash the command."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.side_effect = RuntimeError("boom")
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        err = StringIO()
        call_command("push_to_hub", "--dry-run", stdout=out, stderr=err)
        self.assertIn("boom", err.getvalue())

    @override_settings(HUB_URL="https://hub.example.com", WEBHOOK_SECRET_CLUSTER="test-secret")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_signature_header_sent_when_secret_set(self, mock_urlopen, mock_registry):
        """When WEBHOOK_SECRET_CLUSTER is set, X-Cluster-Signature header is sent."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", stdout=out)

        request = mock_urlopen.call_args[0][0]
        self.assertIn("X-cluster-signature", request.headers)

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_hub_error_raises_command_error(self, mock_urlopen, mock_registry):
        """Hub returning non-2xx should raise CommandError."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 500
        mock_response.read.return_value = b"Internal Server Error"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=StringIO())
        self.assertIn("500", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_network_error_raises_command_error(self, mock_urlopen, mock_registry):
        """Network failure should raise CommandError."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_urlopen.side_effect = ConnectionError("refused")

        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=StringIO())
        self.assertIn("Failed to reach hub", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.urlopen")
    def test_json_output_on_success(self, mock_urlopen, mock_registry):
        """--json with successful POST outputs JSON payload."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 202
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        out = StringIO()
        call_command("push_to_hub", "--json", stdout=out)
        parsed = json.loads(out.getvalue())
        self.assertEqual(parsed["source"], "cluster")

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_result_to_alert_critical(self, mock_registry):
        """CRITICAL check result maps to firing/critical alert."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.CRITICAL,
            message="CPU at 99%",
            metrics={"cpu_percent": 99.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        alert = payload["alerts"][0]
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "critical")

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY", {"cpu": MagicMock()})
    def test_unknown_checker_raises_command_error(self):
        """--checkers with an unknown name raises CommandError."""
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", "--checkers", "nonexistent", stdout=out)
        self.assertIn("nonexistent", str(ctx.exception))
        self.assertIn("Unknown checker", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch(
        "apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY",
        {"cpu": MagicMock(), "memory": MagicMock()},
    )
    def test_mixed_valid_and_invalid_checkers_raises_command_error(self):
        """--checkers with mixed valid/invalid names reports all unknown checkers."""
        out = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", "--checkers", "cpu,bad1,memory,bad2", stdout=out)
        error_msg = str(ctx.exception)
        self.assertIn("bad1", error_msg)
        self.assertIn("bad2", error_msg)
        self.assertIn("Unknown checker", error_msg)

    @override_settings(HUB_URL="file:///etc/passwd")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_rejects_non_http_scheme(self, mock_registry):
        """HUB_URL with file:// or other non-http scheme should be rejected."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=StringIO())
        self.assertIn("http", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_result_to_alert_unknown_status(self, mock_registry):
        """UNKNOWN check result maps to firing/warning alert."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.UNKNOWN,
            message="Check failed",
            metrics={},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        out = StringIO()
        call_command("push_to_hub", "--dry-run", "--json", stdout=out)
        payload = json.loads(out.getvalue())
        alert = payload["alerts"][0]
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["severity"], "warning")
