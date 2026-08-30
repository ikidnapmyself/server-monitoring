import json
from io import StringIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.alerts.check_integration import CheckAlertBridge
from apps.alerts.management.commands.push_to_hub import Command
from apps.alerts.models import Alert, Node
from apps.checkers.checkers.base import CheckResult, CheckStatus
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineOrigin,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
)
from config.security.url_validation import URLNotAllowedError


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

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
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

    @override_settings(
        HUB_URL="https://hub.example.com", INSTANCE_ID="test-agent", HUB_API_KEY="tok123"
    )
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
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

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_push_sends_bearer_key_and_no_signature(self, mock_urlopen, mock_registry):
        """The push authenticates with Authorization: Bearer HUB_API_KEY, no HMAC."""
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
        self.assertEqual(request.headers["Authorization"], "Bearer tok123")
        lowered = {k.lower(): v for k, v in request.headers.items()}
        self.assertNotIn("x-cluster-signature", lowered)

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_push_sends_identifiable_user_agent(self, mock_urlopen, mock_registry):
        """An explicit User-Agent is sent so a WAF doesn't block the default
        urllib UA (Python-urllib/*), which commonly yields a 403."""
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

        call_command("push_to_hub", stdout=StringIO())

        request = mock_urlopen.call_args[0][0]
        ua = {k.lower(): v for k, v in request.headers.items()}.get("user-agent", "")
        self.assertIn("server-monitoring-agent", ua)
        self.assertNotIn("python-urllib", ua.lower())

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    def test_push_errors_without_api_key(self, mock_registry):
        """A real push with no HUB_API_KEY fails clearly rather than posting anonymously."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=StringIO())
        self.assertIn("HUB_API_KEY", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_unaccepted_2xx_status_raises_command_error(self, mock_urlopen, mock_registry):
        """A 2xx response outside {200,201,202} does not raise HTTPError in urllib,
        so it reaches the else: branch and is reported as an HTTP failure."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]

        mock_response = MagicMock()
        mock_response.status = 204
        mock_response.read.return_value = b""
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=err)
        self.assertIn("204", str(ctx.exception))
        self.assertIn("push FAILED", err.getvalue())
        self.assertIn("HTTP 204", err.getvalue())

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
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

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
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

    @staticmethod
    def _ok_response(status=202, body=b"ok"):
        resp = MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_default_output_is_summary_not_payload(self, mock_urlopen, mock_registry):
        """Default (no-flag) push prints the summary, never the payload/metrics."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.WARNING,
            message="CPU at 75%",
            metrics={"cpu_percent": 75.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_urlopen.return_value = self._ok_response(status=202)

        out = StringIO()
        call_command("push_to_hub", stdout=out)
        output = out.getvalue()

        self.assertIn("push OK", output)
        self.assertIn("HTTP 202", output)
        self.assertIn("firing: cpu(warning)", output)
        self.assertNotIn("cpu_percent", output)
        self.assertNotIn('"metrics"', output)

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_failed_http_writes_summary_to_stderr_and_raises(self, mock_urlopen, mock_registry):
        """A real 4xx/5xx (urllib raises HTTPError) writes a push FAILED HTTP summary."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_urlopen.side_effect = HTTPError(
            "https://hub.example.com", 500, "Server Error", {}, None
        )

        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=err)
        # Classified as an HTTP failure, not "unreachable".
        self.assertIn("push FAILED", err.getvalue())
        self.assertIn("HTTP 500", err.getvalue())
        self.assertNotIn("unreachable", err.getvalue())
        self.assertIn("ms)", err.getvalue())
        self.assertIn("500", str(ctx.exception))

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_json_flag_still_dumps_payload(self, mock_urlopen, mock_registry):
        """--json preserves the full payload dump (needed for debugging)."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={"cpu_percent": 10.0}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_urlopen.return_value = self._ok_response(status=202)

        out = StringIO()
        call_command("push_to_hub", "--json", stdout=out)
        self.assertIn("cpu_percent", out.getvalue())

    @override_settings(HUB_URL="https://hub.example.com", HUB_API_KEY="tok123")
    @patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_unreachable_writes_summary_to_stderr_and_raises(self, mock_urlopen, mock_registry):
        """A transport failure writes an 'unreachable' summary to stderr and raises."""
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_urlopen.side_effect = URLError("timed out")

        err = StringIO()
        with self.assertRaises(CommandError):
            call_command("push_to_hub", stderr=err)
        self.assertIn("unreachable:", err.getvalue())
        self.assertIn("ms)", err.getvalue())


MOCK_REGISTRY = {
    "cpu": MagicMock(
        return_value=MagicMock(
            run=MagicMock(
                return_value=CheckResult(
                    status=CheckStatus.OK, message="OK", metrics={}, checker_name="cpu"
                )
            )
        )
    )
}


@patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY", MOCK_REGISTRY)
class TestPushToHubSSRF(TestCase):
    @override_settings(HUB_URL="http://10.0.0.1", HUB_API_KEY="tok123")
    @patch(
        "apps.alerts.management.commands.push_to_hub.safe_urlopen",
        side_effect=URLNotAllowedError("private"),
    )
    def test_private_hub_url_rejected(self, _mock_urlopen):
        err = StringIO()
        with self.assertRaises(CommandError) as ctx:
            call_command("push_to_hub", stderr=err)
        self.assertIn("not allowed", str(ctx.exception).lower())
        self.assertIn("push FAILED", err.getvalue())
        self.assertIn("URL not allowed", err.getvalue())


class SummarizePushTests(TestCase):
    """Tests for the pure summarize_push helper (no I/O, no secrets)."""

    @staticmethod
    def _alert(checker, status, severity, metrics=None):
        return {
            "name": f"{checker}: msg",
            "status": status,
            "severity": severity,
            "labels": {"checker": checker},
            "metrics": metrics or {},
        }

    def test_success_mixed_counts_and_firing_order(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        alerts = [
            self._alert("cpu", "resolved", "info"),
            self._alert("disk_linux", "firing", "warning"),
            self._alert("raid", "firing", "critical"),
        ]
        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=alerts,
            http_status=202,
            duration_ms=312,
            ok=True,
        )
        self.assertIn("push OK", out)
        self.assertIn("hub=https://hub.example.com", out)
        self.assertIn("ok=1 warning=1 critical=1 -> 3 alerts", out)
        self.assertIn("HTTP 202", out)
        self.assertIn("(312ms)", out)
        self.assertIn("firing: raid(critical), disk_linux(warning)", out)

    def test_all_ok_has_no_firing_line(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        alerts = [self._alert("cpu", "resolved", "info")]
        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=alerts,
            http_status=202,
            duration_ms=5,
            ok=True,
        )
        self.assertIn("push OK", out)
        self.assertNotIn("firing:", out)

    def test_no_duration_omits_ms(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[self._alert("cpu", "resolved", "info")],
            http_status=202,
            duration_ms=None,
            ok=True,
        )
        self.assertNotIn("ms)", out)

    def test_failure_http_status(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[],
            http_status=500,
            duration_ms=None,
            ok=False,
        )
        self.assertIn("push FAILED", out)
        self.assertIn("HTTP 500", out)

    def test_failure_unreachable(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[],
            http_status=None,
            duration_ms=None,
            ok=False,
            error="timed out",
        )
        self.assertIn("push FAILED", out)
        self.assertIn("unreachable: timed out", out)
        self.assertNotIn("ms)", out)

    def test_failure_includes_duration_when_provided(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        unreachable = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[],
            http_status=None,
            duration_ms=30001,
            ok=False,
            error="timed out",
        )
        self.assertIn("unreachable: timed out (30001ms)", unreachable)

    def test_failure_unreachable_without_error_is_actionable(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[],
            http_status=None,
            duration_ms=None,
            ok=False,
        )
        # No None leaks into the message when the error is missing.
        self.assertIn("unreachable: unknown error", out)
        self.assertNotIn("None", out)

    def test_hub_url_credentials_and_query_are_redacted(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        secret_url = "https://alice:s3cr3t@hub.example.com:8443/ingest?token=leakme#frag"
        for ok in (True, False):
            out = summarize_push(
                hub_url=secret_url,
                alerts=[],
                http_status=202 if ok else 500,
                duration_ms=1,
                ok=ok,
            )
            # Credentials, query params, and fragment must never reach the log.
            self.assertNotIn("s3cr3t", out)
            self.assertNotIn("alice", out)
            self.assertNotIn("leakme", out)
            self.assertNotIn("token=", out)
            # Host, port, and path are kept for operational usefulness.
            self.assertIn("hub=https://hub.example.com:8443/ingest", out)

        http = summarize_push(
            hub_url="https://hub.example.com",
            alerts=[],
            http_status=500,
            duration_ms=12,
            ok=False,
        )
        self.assertIn("HTTP 500 (12ms)", http)

    def test_does_not_leak_metrics_or_secrets(self):
        from apps.alerts.management.commands.push_to_hub import summarize_push

        alerts = [
            self._alert(
                "cpu", "firing", "warning", metrics={"secret_metric": "sensitive-value-XYZ"}
            )
        ]
        out = summarize_push(
            hub_url="https://hub.example.com",
            alerts=alerts,
            http_status=202,
            duration_ms=10,
            ok=True,
        )
        self.assertNotIn("sensitive-value-XYZ", out)
        self.assertNotIn("metrics", out)


class SendToHubHelpersTests(TestCase):
    """Direct tests for the shared helpers reused by setup_cluster."""

    def test_build_cluster_payload(self):
        from apps.alerts.management.commands.push_to_hub import build_cluster_payload

        p = build_cluster_payload("web-03", "web-03.local", [{"x": 1}])
        self.assertEqual(p["source"], "cluster")
        self.assertEqual(p["instance_id"], "web-03")
        self.assertEqual(p["hostname"], "web-03.local")
        self.assertEqual(p["alerts"], [{"x": 1}])

    def test_send_to_hub_rejects_bad_scheme(self):
        from apps.alerts.management.commands.push_to_hub import send_to_hub

        with self.assertRaises(ValueError):
            send_to_hub("file:///etc/passwd", "tok", {"source": "cluster"})

    @override_settings(SSRF_ALLOWED_HOSTS=["hub.example.com"])
    @patch("apps.alerts.management.commands.push_to_hub.safe_urlopen")
    def test_send_to_hub_returns_status_and_body(self, mock_urlopen):
        from apps.alerts.management.commands.push_to_hub import send_to_hub

        resp = MagicMock()
        resp.status = 202
        resp.read.return_value = b'{"ok": true}'
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        status, body = send_to_hub(
            "https://hub.example.com", "tok", {"source": "cluster", "alerts": []}
        )
        self.assertEqual(status, 202)
        self.assertIn("ok", body)


class ResultToAlertIdentityTests(TestCase):
    """The pushed alert must carry the same identity CheckAlertBridge writes."""

    @staticmethod
    def _result(status=CheckStatus.OK, message="CPU OK"):
        return CheckResult(
            status=status,
            message=message,
            metrics={"cpu_percent": 91.0},
            checker_name="cpu",
        )

    def test_pushed_alert_shares_the_bridge_fingerprint(self):
        """Fingerprint is keyed on instance_id, not the collidable hostname."""
        alert = Command()._result_to_alert(self._result(), "node-a", "host-a")
        self.assertEqual(alert["fingerprint"], "check:node-a:cpu")

    def test_pushed_alert_name_matches_the_bridge_name(self):
        """Name is the bridge's stable form, not the live message."""
        alert = Command()._result_to_alert(self._result(), "node-a", "host-a")
        self.assertEqual(alert["name"], "CPU Check Alert")

    def test_name_is_stable_across_message_changes(self):
        """Incident grouping matches on name, so it must not drift per tick."""
        first = Command()._result_to_alert(
            self._result(message="CPU usage 91%"), "node-a", "host-a"
        )
        second = Command()._result_to_alert(
            self._result(message="CPU usage 94%"), "node-a", "host-a"
        )
        self.assertEqual(first["name"], second["name"])

    def test_live_message_survives_in_the_description(self):
        """The metric text moves to the description; it is not lost."""
        alert = Command()._result_to_alert(
            self._result(message="CPU usage 91%"), "node-a", "host-a"
        )
        self.assertEqual(alert["description"], "CPU usage 91%")

    def test_both_producers_agree_on_identity(self):
        """The invariant: one condition on one machine is one Alert row."""
        result = self._result(status=CheckStatus.CRITICAL, message="CPU usage 99%")
        pushed = Command()._result_to_alert(result, "node-a", "host-a")
        bridged = CheckAlertBridge(
            hostname="host-a", instance_id="node-a", register_node=False
        ).check_result_to_parsed_alert(result)
        self.assertEqual(pushed["fingerprint"], bridged.fingerprint)
        self.assertEqual(pushed["name"], bridged.name)


@override_settings(INSTANCE_ID="hub-1", HUB_URL="")
class PushToHubLocalTests(TestCase):
    """``--local`` produces truth and enqueues, like every other producer.

    Writing an alert is not a pipeline stage, so ``--local`` records the alerts
    itself through ``CheckAlertBridge`` — the same writer ``check_health`` uses —
    and then enqueues one run per materially changed incident. It is the queued
    mode: ``process_inbox`` completes it.
    """

    def setUp(self):
        patcher = patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
        mock_registry = patcher.start()
        self.addCleanup(patcher.stop)
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.CRITICAL,
            message="CPU usage 99%",
            metrics={"cpu_percent": 99.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_registry.__contains__.return_value = True

    def _push_local(self, *extra):
        out = StringIO()
        call_command("push_to_hub", "--local", "--checkers", "cpu", *extra, stdout=out)
        return out.getvalue()

    def test_local_writes_alerts_directly(self):
        """The alert exists the moment the command returns — no drain needed."""
        self._push_local()
        alert = Alert.objects.get()
        self.assertEqual(alert.fingerprint, "check:hub-1:cpu")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.source, CheckAlertBridge.SOURCE_NAME)

    def test_local_records_no_driver_payload_run(self):
        """The ``{driver, payload}`` wrapper is gone; the run carries an incident."""
        self._push_local()
        incident = Alert.objects.get().incident
        run = PipelineRun.objects.get()
        self.assertNotIn("driver", run.inbound_payload)
        self.assertNotIn("payload", run.inbound_payload)
        self.assertEqual(run.inbound_payload, {"downstream_incident_id": incident.id})

    def test_local_enqueues_one_pending_run_per_material_incident(self):
        self._push_local()
        incident = Alert.objects.get().incident
        self.assertIsNotNone(incident)
        run = PipelineRun.objects.get()
        self.assertEqual(run.incident_id, incident.id)
        self.assertEqual(run.origin, PipelineOrigin.CHECKER_GENERATED)
        self.assertEqual(run.source, CheckAlertBridge.SOURCE_NAME)

    def test_local_leaves_the_runs_pending(self):
        """--local is the queued mode; ``process_inbox`` completes it, not this."""
        self._push_local()
        self.assertEqual(PipelineRun.objects.get().status, PipelineStatus.PENDING)

    def test_local_registers_this_machine(self):
        """A local push proves this machine is alive, exactly like a peer's."""
        self._push_local()
        self.assertTrue(Node.objects.filter(instance_id="hub-1").exists())

    def test_local_registration_is_marked_local_not_cluster(self):
        """``last_source`` tells fleet from self, so --local must not say cluster.

        ``cluster`` means the row arrived by push from another machine. A hub
        running both --local and check_health would otherwise flip the field
        between the two every run, and anything reading it would be wrong half
        the time.
        """
        self._push_local()
        self.assertEqual(Node.objects.get(instance_id="hub-1").last_source, "local")

    def test_local_still_needs_no_hub_url(self):
        """A hub checking itself has no hub to point at."""
        self._push_local()
        self.assertEqual(PipelineRun.objects.count(), 1)

    @patch("apps.alerts.management.commands.push_to_hub.send_to_hub")
    def test_local_sends_no_http(self, mock_send):
        self._push_local()
        mock_send.assert_not_called()

    def test_local_json_shape(self):
        """--json means a wrapper parses stdout; --local must honour it too.

        ``run_id`` is not singular any more: a push can materially change several
        incidents or none, so the shape reports the trace and the incidents.
        """
        parsed = json.loads(self._push_local("--json"))
        incident = Alert.objects.get().incident
        run = PipelineRun.objects.get()
        self.assertEqual(parsed["trace_id"], run.trace_id)
        self.assertEqual(parsed["incidents"], [incident.id])
        self.assertEqual(parsed["alerts"], 1)

    def test_local_reports_a_failed_alert_write_on_stderr(self):
        """A bridge that could not write must say so, not exit quietly at 0.

        The bridge catches what the alert write raises and folds the message into
        its result, so ignoring the return value would swallow the one signal an
        operator gets — and --json parses stdout, so the report belongs on stderr.
        """
        from apps.alerts.check_integration import CheckAlertResult

        broken = MagicMock()
        broken.return_value.process_check_results.return_value = CheckAlertResult(
            errors=["no such table: alerts_alert"]
        )
        err = StringIO()
        with patch("apps.alerts.check_integration.CheckAlertBridge", broken):
            call_command(
                "push_to_hub", "--local", "--checkers", "cpu", stdout=StringIO(), stderr=err
            )

        self.assertIn("no such table: alerts_alert", err.getvalue())
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_local_registers_this_machine_when_every_checker_fails(self):
        """A push proves the sender is alive whatever its alerts turn out to be.

        The registry must not depend on them — that is the invariant the hub path
        states explicitly — so a run whose every checker raised still puts this
        machine in the registry. Otherwise the one machine that most needs to be
        visible is the one that disappears.
        """
        broken = MagicMock()
        broken.return_value.run.side_effect = RuntimeError("checker exploded")
        with patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY") as mock_registry:
            mock_registry.items.return_value = [("cpu", broken)]
            mock_registry.__contains__.return_value = True
            call_command(
                "push_to_hub",
                "--local",
                "--checkers",
                "cpu",
                stdout=StringIO(),
                stderr=StringIO(),
            )

        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(Node.objects.get(instance_id="hub-1").last_source, "local")

    def test_dry_run_with_local_records_nothing(self):
        out = self._push_local("--dry-run")
        self.assertIn("dry run", out.lower())
        self.assertEqual(PipelineRun.objects.count(), 0)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(Node.objects.count(), 0)


@override_settings(INSTANCE_ID="hub-1", HUB_URL="")
class PushToHubLocalDrainTests(TestCase):
    """End to end: the queued work is usable, not merely well-shaped.

    A shape assertion cannot tell an enqueued incident run from one nothing can
    execute. Draining it shows the incident reaches its lane's stages.
    """

    def setUp(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.seeding import enable_delivery

        patcher = patch("apps.alerts.management.commands.push_to_hub.CHECKER_REGISTRY")
        mock_registry = patcher.start()
        self.addCleanup(patcher.stop)
        mock_checker_cls = MagicMock()
        mock_checker_cls.return_value.run.return_value = CheckResult(
            status=CheckStatus.CRITICAL,
            message="CPU usage 99%",
            metrics={"cpu_percent": 99.0},
            checker_name="cpu",
        )
        mock_registry.items.return_value = [("cpu", mock_checker_cls)]
        mock_registry.__contains__.return_value = True

        # The seeded lanes record rather than deliver on a database with no
        # channel; this test asserts the drain end to end, so it is the
        # configured hub it describes. The generic driver treats an empty config
        # as a no-op, so NOTIFY runs for real without touching the network.
        channel = NotificationChannel.objects.create(
            name="ops", driver="generic", is_active=True, config={}
        )
        enable_delivery(PipelineDefinition, channel)

    def test_local_run_drains_to_completion(self):
        from apps.orchestration import inbox

        call_command("push_to_hub", "--local", "--checkers", "cpu", stdout=StringIO())
        run = PipelineRun.objects.get()
        trace_id = run.trace_id

        while inbox.drain(limit=10):
            pass

        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.NOTIFIED)
        stages = list(
            StageExecution.objects.filter(pipeline_run__trace_id=trace_id)
            .order_by("id")
            .values_list("stage", flat=True)
        )
        # The alerts are already written, so no INGEST stage exists to re-run
        # them; the cluster lane skips CHECK, leaving its analyse/notify pair.
        self.assertEqual(stages, [PipelineStage.ANALYZE, PipelineStage.NOTIFY])
        incident = Alert.objects.get().incident
        self.assertEqual(incident.pipeline.name, "cluster-nodes")
