"""Tests for the test_notify management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.notify.models import NotificationChannel

SEND_OK = {"success": True, "message_id": "m1", "metadata": {}}
SEND_OK_META = {"success": True, "message_id": "abc-123", "metadata": {"channel": "#alerts"}}
SEND_FAIL = {"success": False, "error": "Connection refused"}


def _slack_channel(**overrides):
    defaults = {
        "name": "ops-slack",
        "driver": "slack",
        "config": {"webhook_url": "https://hooks.slack.com/services/T/B/X"},
    }
    defaults.update(overrides)
    return NotificationChannel.objects.create(**defaults)


def _generic_channel(**overrides):
    defaults = {
        "name": "ops-generic",
        "driver": "generic",
        "config": {"endpoint": "https://example.com/hook"},
    }
    defaults.update(overrides)
    return NotificationChannel.objects.create(**defaults)


class NonInteractiveTests(TestCase):
    """Tests for --non-interactive flag preserving existing behavior."""

    def test_non_interactive_with_db_channel_sends(self):
        _slack_channel()
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value=SEND_OK,
        ):
            call_command("test_notify", "ops-slack", "--non-interactive", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_unknown_driver_raises(self):
        with self.assertRaises(Exception):
            call_command(
                "test_notify",
                "nonexistent",
                "--non-interactive",
                stdout=StringIO(),
                stderr=StringIO(),
            )

    def test_non_interactive_no_driver_uses_first_channel(self):
        _slack_channel(name="alpha")
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value=SEND_OK,
        ):
            call_command("test_notify", "--non-interactive", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_json_config_override(self):
        _generic_channel()
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "generic",
                "--non-interactive",
                "--json-config",
                '{"endpoint": "https://new.example.com"}',
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_default_mode_is_interactive(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("Test Notification Wizard", out.getvalue())


class SelectChannelTests(TestCase):
    """Tests for interactive channel selection."""

    def test_lists_active_channels(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("ops-slack", output)
        self.assertIn("slack", output)

    def test_no_channels_shows_configure_only(self):
        out = StringIO()
        # Only option is "Configure new" → pick generic (4th) → endpoint → defaults → done
        with (
            patch(
                "builtins.input",
                side_effect=["1", "4", "https://example.com/hook", "", "", "", "3"],
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("Configure a new driver", out.getvalue())

    def test_invalid_selection_retries(self):
        _slack_channel()
        out = StringIO()
        err = StringIO()
        with (
            patch("builtins.input", side_effect=["99", "1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
        ):
            call_command("test_notify", stdout=out, stderr=err)
        self.assertIn("Invalid selection", err.getvalue())
        self.assertIn("ops-slack", out.getvalue())


class ConfigureNewDriverTests(TestCase):
    """Tests for interactive new-driver configuration."""

    def test_configure_slack(self):
        out = StringIO()
        # No channels → configure new (1) → slack (2) → webhook → defaults → done
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "2",
                    "https://hooks.slack.com/services/T/B/X",
                    "",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_configure_generic(self):
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=["1", "4", "https://example.com/hook", "", "", "", "3"],
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_configure_pagerduty(self):
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "3",
                    "abcdefghijklmnopqrstuvwxyz",
                    "",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_configure_email(self):
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "1",
                    "smtp.example.com",
                    "alerts@example.com",
                    "587",
                    "",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("successfully", out.getvalue())


class MessagePromptTests(TestCase):
    """Tests for message option prompting."""

    def test_defaults_accepted_on_enter(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ) as mock_send,
        ):
            call_command("test_notify", stdout=out)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg.title, "Test Alert")
        self.assertEqual(msg.severity, "info")

    def test_custom_values_used(self):
        _slack_channel()
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=["1", "Deploy Alert", "Deploying v2.0", "warning", "3"],
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ) as mock_send,
        ):
            call_command("test_notify", stdout=out)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg.title, "Deploy Alert")
        self.assertEqual(msg.message, "Deploying v2.0")
        self.assertEqual(msg.severity, "warning")


class SendResultDisplayTests(TestCase):
    """Tests for send result display."""

    def test_success_shows_message_id(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK_META,
            ),
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("successfully", output)
        self.assertIn("abc-123", output)

    def test_failure_shows_error(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_FAIL,
            ),
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("Failed", output)
        self.assertIn("Connection refused", output)

    def test_invalid_config_shows_error(self):
        NotificationChannel.objects.create(name="bad-ch", driver="slack", config={})
        out = StringIO()
        err = StringIO()
        with patch("builtins.input", side_effect=["1", "", "", "", "3"]):
            call_command("test_notify", stdout=out, stderr=err)
        self.assertIn("Invalid configuration", err.getvalue())


class PostSendLoopTests(TestCase):
    """Tests for the retry/switch/done loop after sending."""

    def test_retry_resends_with_new_options(self):
        _slack_channel()
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "",
                    "",
                    "",
                    "1",
                    "Retry Alert",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ) as mock_send,
        ):
            call_command("test_notify", stdout=out)
        self.assertEqual(mock_send.call_count, 2)
        second_msg = mock_send.call_args_list[1][0][0]
        self.assertEqual(second_msg.title, "Retry Alert")

    def test_switch_channel_resends(self):
        _slack_channel(name="ch-a")
        _generic_channel(name="ch-b")
        out = StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "",
                    "",
                    "",
                    "2",
                    "2",
                    "",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("ch-a", output)
        self.assertIn("ch-b", output)

    def test_done_exits_immediately(self):
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ) as mock_send,
        ):
            call_command("test_notify", stdout=out)
        self.assertEqual(mock_send.call_count, 1)


class PromptEdgeCaseTests(TestCase):
    """Tests for prompt helper edge cases."""

    def test_prompt_input_required_retries_on_empty(self):
        """Required field retries when user enters empty, then succeeds."""
        out = StringIO()
        err = StringIO()
        # Configure new → email → empty smtp_host → then valid → rest of flow
        with (
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "1",
                    "",
                    "smtp.example.com",
                    "alerts@example.com",
                    "587",
                    "",
                    "",
                    "",
                    "3",
                ],
            ),
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command("test_notify", stdout=out, stderr=err)
        self.assertIn("required", err.getvalue())
        self.assertIn("successfully", out.getvalue())

    def test_prompt_choice_non_numeric_retries(self):
        """Non-numeric choice input retries."""
        _slack_channel()
        err = StringIO()
        with (
            patch("builtins.input", side_effect=["abc", "1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
        ):
            call_command("test_notify", stdout=StringIO(), stderr=err)
        self.assertIn("Invalid selection", err.getvalue())

    def test_unknown_driver_interactive_raises(self):
        """Interactive _send_and_show_result with unknown driver raises."""
        # Create channel with a driver that's not in DRIVER_REGISTRY
        NotificationChannel.objects.create(name="bad-driver", driver="nonexistent", config={})
        with (
            patch("builtins.input", side_effect=["1", "", "", ""]),
            self.assertRaises(Exception),
        ):
            call_command("test_notify", stdout=StringIO())


class NonInteractiveBranchTests(TestCase):
    """Tests to cover _handle_non_interactive branches."""

    def test_non_interactive_invalid_json_config_raises(self):
        """--non-interactive with invalid JSON config raises."""
        _slack_channel()
        with self.assertRaises(Exception):
            call_command(
                "test_notify",
                "ops-slack",
                "--non-interactive",
                "--json-config",
                "{bad json",
                stdout=StringIO(),
            )

    def test_non_interactive_invalid_config_raises(self):
        """--non-interactive with invalid driver config raises."""
        with self.assertRaises(Exception):
            call_command(
                "test_notify",
                "slack",
                "--non-interactive",
                stdout=StringIO(),
            )

    def test_non_interactive_send_failure(self):
        """--non-interactive shows failure result."""
        _slack_channel()
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value=SEND_FAIL,
        ):
            call_command("test_notify", "ops-slack", "--non-interactive", stdout=out)
        self.assertIn("Failed", out.getvalue())

    def test_non_interactive_send_with_metadata(self):
        """--non-interactive shows metadata on success."""
        _slack_channel()
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value=SEND_OK_META,
        ):
            call_command("test_notify", "ops-slack", "--non-interactive", stdout=out)
        output = out.getvalue()
        self.assertIn("abc-123", output)
        self.assertIn("#alerts", output)

    def test_non_interactive_email_driver_flags(self):
        """--non-interactive with email driver CLI flags builds config."""
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.email.EmailNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "email",
                "--non-interactive",
                "--smtp-host",
                "smtp.example.com",
                "--from-address",
                "test@example.com",
                "--use-tls",
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_slack_driver_flags(self):
        """--non-interactive with slack driver CLI flags builds config."""
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "slack",
                "--non-interactive",
                "--webhook-url",
                "https://hooks.slack.com/services/T/B/X",
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_pagerduty_driver_flags(self):
        """--non-interactive with pagerduty driver CLI flags."""
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "pagerduty",
                "--non-interactive",
                "--integration-key",
                "abcdefghijklmnopqrstuvwxyz",
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_generic_driver_flags(self):
        """--non-interactive with generic driver CLI flags."""
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "generic",
                "--non-interactive",
                "--endpoint",
                "https://example.com/hook",
                "--api-key",
                "secret",
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_build_config_json_in_build(self):
        """--non-interactive _build_config with json_config in options."""
        out = StringIO()
        with (
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "slack",
                "--non-interactive",
                "--json-config",
                '{"webhook_url": "https://hooks.slack.com/services/T/B/X"}',
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_build_config_unknown_driver(self):
        """_build_config with unknown driver returns empty dict."""
        with self.assertRaises(Exception):
            call_command(
                "test_notify",
                "unknown_driver",
                "--non-interactive",
                stdout=StringIO(),
            )


class BuildConfigFallbackTests(TestCase):
    """Tests for _build_config methods via NotifySelector fallback.

    These methods are only reached when NotifySelector.resolve returns
    None for driver_class (e.g., custom provider not in views registry).
    We mock the selector to force this path.
    """

    def _force_fallback(self, driver, extra_flags, send_mock_path, validate_mock_path=None):
        """Call with mocked NotifySelector to trigger _build_config fallback."""
        from contextlib import ExitStack

        out = StringIO()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "apps.notify.services.NotifySelector.resolve",
                    return_value=(driver, {}, driver, None, None, "default"),
                )
            )
            stack.enter_context(patch(send_mock_path, return_value=SEND_OK))
            if validate_mock_path:
                stack.enter_context(patch(validate_mock_path, return_value=True))
            call_command("test_notify", driver, "--non-interactive", *extra_flags, stdout=out)
        return out.getvalue()

    def test_build_email_config_fallback(self):
        output = self._force_fallback(
            "email",
            [
                "--smtp-host",
                "smtp.test.com",
                "--from-address",
                "a@b.c",
                "--smtp-port",
                "465",
                "--use-tls",
            ],
            "apps.notify.drivers.email.EmailNotifyDriver.send",
            "apps.notify.drivers.email.EmailNotifyDriver.validate_config",
        )
        self.assertIn("successfully", output)

    def test_build_slack_config_fallback(self):
        output = self._force_fallback(
            "slack",
            ["--webhook-url", "https://hooks.slack.com/services/T/B/X", "--channel", "#ops"],
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            "apps.notify.drivers.slack.SlackNotifyDriver.validate_config",
        )
        self.assertIn("successfully", output)

    def test_build_pagerduty_config_fallback(self):
        output = self._force_fallback(
            "pagerduty",
            ["--integration-key", "abcdefghijklmnopqrstuvwxyz"],
            "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.send",
            "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.validate_config",
        )
        self.assertIn("successfully", output)

    def test_build_generic_config_fallback(self):
        output = self._force_fallback(
            "generic",
            ["--endpoint", "https://example.com/hook", "--api-key", "secret"],
            "apps.notify.drivers.generic.GenericNotifyDriver.send",
            "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
        )
        self.assertIn("successfully", output)

    def test_build_config_json_fallback(self):
        """_build_config prefers json_config when available."""
        out = StringIO()
        with (
            patch(
                "apps.notify.services.NotifySelector.resolve",
                return_value=("slack", {}, "slack", None, None, "default"),
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value=SEND_OK,
            ),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.validate_config",
                return_value=True,
            ),
        ):
            call_command(
                "test_notify",
                "slack",
                "--non-interactive",
                "--json-config",
                '{"webhook_url": "https://hooks.slack.com/services/T/B/X"}',
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_build_config_invalid_json_fallback_raises(self):
        """_build_config with invalid JSON in fallback path raises."""
        with (
            patch(
                "apps.notify.services.NotifySelector.resolve",
                return_value=("slack", {}, "slack", None, None, "default"),
            ),
            self.assertRaises(Exception),
        ):
            call_command(
                "test_notify",
                "slack",
                "--non-interactive",
                "--json-config",
                "{bad",
                stdout=StringIO(),
            )

    def test_build_config_unknown_driver_fallback(self):
        """_build_config with unknown driver in fallback returns empty."""
        with (
            patch(
                "apps.notify.services.NotifySelector.resolve",
                return_value=("custom", {}, "custom", None, None, "default"),
            ),
            self.assertRaises(Exception),
        ):
            call_command(
                "test_notify",
                "custom",
                "--non-interactive",
                stdout=StringIO(),
            )


class InteractiveMiscTests(TestCase):
    """Tests for remaining interactive branches."""

    def test_prompt_input_no_default_not_required_returns_empty(self):
        """_prompt_input returns '' when input is empty, no default, not required."""
        from apps.notify.management.commands.test_notify import Command

        cmd = Command(stdout=StringIO(), stderr=StringIO())
        with patch("builtins.input", return_value=""):
            result = cmd._prompt_input("Optional field")
        self.assertEqual(result, "")

    def test_build_config_interactive_unknown_driver(self):
        """_build_config_interactive returns {} for unknown driver name."""
        from apps.notify.management.commands.test_notify import Command

        cmd = Command(stdout=StringIO(), stderr=StringIO())
        result = cmd._build_config_interactive("unknown")
        self.assertEqual(result, {})

    def test_interactive_success_without_message_id(self):
        """Interactive send shows success but skips Message ID when empty."""
        _slack_channel()
        out = StringIO()
        with (
            patch("builtins.input", side_effect=["1", "", "", "", "3"]),
            patch(
                "apps.notify.drivers.slack.SlackNotifyDriver.send",
                return_value={"success": True, "message_id": "", "metadata": {}},
            ),
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("successfully", output)
        self.assertNotIn("Message ID:", output)


class DirectBuildConfigTests(TestCase):
    """Direct tests for _build_*_config methods to cover false branches."""

    def _cmd(self):
        from apps.notify.management.commands.test_notify import Command

        return Command(stdout=StringIO(), stderr=StringIO())

    def test_build_email_config_no_flags(self):
        """_build_email_config with no flags returns empty config."""
        config = self._cmd()._build_email_config({})
        self.assertEqual(config, {})

    def test_build_email_config_only_smtp_host(self):
        """_build_email_config with only smtp_host omits others."""
        config = self._cmd()._build_email_config({"smtp_host": "mail.test.com"})
        self.assertEqual(config, {"smtp_host": "mail.test.com"})

    def test_build_slack_config_no_flags(self):
        """_build_slack_config with no flags returns empty config."""
        config = self._cmd()._build_slack_config({})
        self.assertEqual(config, {})

    def test_build_slack_config_only_webhook(self):
        """_build_slack_config with only webhook_url omits channel."""
        config = self._cmd()._build_slack_config({"webhook_url": "https://hooks.slack.com/x"})
        self.assertEqual(config, {"webhook_url": "https://hooks.slack.com/x"})

    def test_build_pagerduty_config_no_flags(self):
        """_build_pagerduty_config with no flags returns empty config."""
        config = self._cmd()._build_pagerduty_config({})
        self.assertEqual(config, {})

    def test_build_generic_config_no_flags(self):
        """_build_generic_config with no flags returns empty config."""
        config = self._cmd()._build_generic_config({})
        self.assertEqual(config, {})

    def test_build_generic_config_only_endpoint(self):
        """_build_generic_config with only endpoint omits api_key."""
        config = self._cmd()._build_generic_config({"endpoint": "https://example.com/hook"})
        self.assertEqual(config, {"endpoint": "https://example.com/hook"})
