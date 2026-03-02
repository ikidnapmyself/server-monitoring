"""
Management command to test notification delivery to various backends.

Usage:
    python manage.py test_notify                        # Interactive wizard (default)
    python manage.py test_notify --non-interactive email # Non-interactive mode
    python manage.py test_notify --non-interactive slack --webhook-url https://hooks.slack.com/...
    python manage.py test_notify --non-interactive pagerduty --integration-key xyz123
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver
from apps.notify.models import NotificationChannel

# Registry of available drivers
DRIVER_REGISTRY = {
    "email": EmailNotifyDriver,
    "slack": SlackNotifyDriver,
    "pagerduty": PagerDutyNotifyDriver,
    "generic": GenericNotifyDriver,
}


class Command(BaseCommand):
    help = "Test notification delivery to a specific backend"

    def add_arguments(self, parser):
        parser.add_argument(
            "--non-interactive",
            action="store_true",
            default=False,
            help="Run in non-interactive mode (use CLI flags instead of wizard)",
        )

        # Make driver optional: when omitted, pick first active NotificationChannel from DB
        parser.add_argument(
            "driver",
            nargs="?",
            default=None,
            type=str,
            help=(
                f"Notification driver or channel name to test. "
                f"Options: {', '.join(DRIVER_REGISTRY.keys())} "
                f"(optional, will use first active DB channel if omitted)"
            ),
        )
        parser.add_argument(
            "--title",
            type=str,
            default="Test Alert",
            help="Notification title (default: 'Test Alert')",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="This is a test notification from the notify app.",
            help="Notification message",
        )
        parser.add_argument(
            "--severity",
            type=str,
            choices=["critical", "warning", "info", "success"],
            default="info",
            help="Notification severity (default: 'info')",
        )
        parser.add_argument(
            "--channel",
            type=str,
            default="default",
            help="Destination channel (default: 'default')",
        )
        parser.add_argument(
            "--json-config",
            type=str,
            help="Driver configuration as JSON string",
        )

        # Email options
        parser.add_argument(
            "--smtp-host",
            type=str,
            help="SMTP host (email driver)",
        )
        parser.add_argument(
            "--smtp-port",
            type=int,
            default=587,
            help="SMTP port (email driver, default: 587)",
        )
        parser.add_argument(
            "--from-address",
            type=str,
            help="From email address (email driver)",
        )
        parser.add_argument(
            "--use-tls",
            action="store_true",
            help="Use TLS for SMTP (email driver)",
        )

        # Slack options
        parser.add_argument(
            "--webhook-url",
            type=str,
            help="Slack webhook URL (slack driver)",
        )

        # PagerDuty options
        parser.add_argument(
            "--integration-key",
            type=str,
            help="PagerDuty integration key (pagerduty driver)",
        )

        # Generic options
        parser.add_argument(
            "--endpoint",
            type=str,
            help="API endpoint (generic driver)",
        )
        parser.add_argument(
            "--api-key",
            type=str,
            help="API key (generic driver)",
        )

    def handle(self, *args, **options):
        if options.get("non_interactive"):
            return self._handle_non_interactive(*args, **options)
        return self._handle_interactive(**options)

    # ------------------------------------------------------------------
    # Non-interactive mode (original handle() body, unchanged)
    # ------------------------------------------------------------------

    def _handle_non_interactive(self, *args, **options):
        # Centralize provider/channel selection via NotifySelector
        from apps.notify.services import NotifySelector

        driver_arg = options.get("driver")
        requested = driver_arg
        payload_config = None

        (
            provider_name,
            config,
            selected_label,
            driver_class,
            channel_obj,
            final_channel,
        ) = NotifySelector.resolve(requested, payload_config, options.get("channel"))

        # If NotifySelector returned None for driver_class (unknown provider), allow a
        # fallback where the user provided a driver arg and we build config from CLI options
        if not driver_class and driver_arg:
            provider_name = driver_arg
            config = self._build_config(provider_name, options)
            selected_label = provider_name
            driver_class = DRIVER_REGISTRY.get(provider_name)
            # If CLI provided an explicit channel, use it as final_channel
            final_channel = options.get("channel")

        # Build notification message — use final_channel resolved by selector
        message = NotificationMessage(
            title=options["title"],
            message=options["message"],
            severity=options["severity"],
            channel=final_channel,
        )

        if not driver_class:
            raise CommandError(
                f"Unknown provider: {provider_name}. Available: {', '.join(DRIVER_REGISTRY.keys())}"
            )

        driver = driver_class()

        # If CLI provided JSON config explicitly, allow it to override DB-config
        if options.get("json_config"):
            try:
                cli_conf = json.loads(options.get("json_config"))
                config = cli_conf
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON config: {e}")

        if not driver.validate_config(config):
            raise CommandError(
                f"Invalid configuration for {provider_name} driver. Missing required fields or invalid format."
            )

        # Send notification
        self.stdout.write(
            self.style.WARNING(
                f"Sending test notification via {selected_label} (provider={provider_name})..."
            )
        )

        result = driver.send(message, config)

        # Display result. Drivers may return either {'success': True} or {'status': 'success'}
        success = bool(result.get("success") or (result.get("status") == "success"))
        if success:
            self.stdout.write(self.style.SUCCESS("✓ Notification sent successfully!"))
            self.stdout.write(f"  Provider: {provider_name}")
            self.stdout.write(f"  Channel label: {selected_label}")
            self.stdout.write(
                f"  Message ID: {result.get('message_id') or result.get('message_id', '')}"
            )
            if result.get("metadata"):
                self.stdout.write(f"  Metadata: {json.dumps(result['metadata'], indent=2)}")
        else:
            self.stdout.write(self.style.ERROR("✗ Failed to send notification"))
            self.stdout.write(f"  Provider: {provider_name}")
            self.stdout.write(f"  Channel label: {selected_label}")
            self.stdout.write(f"  Error: {result.get('error') or result.get('message') or result}")

    def _build_config(self, driver_name: str, options: dict[str, Any]) -> dict[str, Any]:
        """Build driver configuration from command options."""
        # Allow direct JSON config
        if options.get("json_config"):
            try:
                return json.loads(options["json_config"])
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON config: {e}")

        # Build config based on driver type
        if driver_name == "email":
            return self._build_email_config(options)
        elif driver_name == "slack":
            return self._build_slack_config(options)
        elif driver_name == "pagerduty":
            return self._build_pagerduty_config(options)
        elif driver_name == "generic":
            return self._build_generic_config(options)

        return {}

    def _build_email_config(self, options: dict[str, Any]) -> dict[str, Any]:
        """Build email driver configuration."""
        config = {}

        if options.get("smtp_host"):
            config["smtp_host"] = options["smtp_host"]
        if options.get("from_address"):
            config["from_address"] = options["from_address"]
        if options.get("smtp_port"):
            config["smtp_port"] = options["smtp_port"]
        if options.get("use_tls"):
            config["use_tls"] = True

        return config

    def _build_slack_config(self, options: dict[str, Any]) -> dict[str, Any]:
        """Build Slack driver configuration."""
        config = {}

        if options.get("webhook_url"):
            config["webhook_url"] = options["webhook_url"]
        if options.get("channel"):
            config["channel"] = options["channel"]

        return config

    def _build_pagerduty_config(self, options: dict[str, Any]) -> dict[str, Any]:
        """Build PagerDuty driver configuration."""
        config = {}

        if options.get("integration_key"):
            config["integration_key"] = options["integration_key"]

        return config

    def _build_generic_config(self, options: dict[str, Any]) -> dict[str, Any]:
        """Build generic driver configuration."""
        config = {}

        if options.get("endpoint"):
            config["endpoint"] = options["endpoint"]
        if options.get("api_key"):
            config["api_key"] = options["api_key"]

        return config

    # ------------------------------------------------------------------
    # Interactive wizard helpers
    # ------------------------------------------------------------------

    def _prompt_choice(self, prompt: str, options: list[tuple[str, str]]) -> str:
        """Display a numbered list and return the selected value.

        Args:
            prompt: Header text shown above the numbered list.
            options: List of (value, label) tuples.

        Returns:
            The *value* from the chosen tuple.
        """
        self.stdout.write(f"\n{prompt}")
        for idx, (_value, label) in enumerate(options, 1):
            self.stdout.write(f"  {idx}. {label}")

        while True:
            raw = input("Enter choice: ").strip()
            try:
                choice = int(raw)
                if 1 <= choice <= len(options):
                    return options[choice - 1][0]
            except (ValueError, IndexError):
                pass
            self.stderr.write(
                self.style.ERROR(f"Invalid selection. Please enter 1-{len(options)}.")
            )

    def _prompt_input(
        self,
        prompt: str,
        default: str | None = None,
        required: bool = False,
    ) -> str:
        """Prompt for free-text input with optional default.

        Shows ``[default]`` suffix when a default is provided.  Retries when
        *required* is ``True`` and the user supplies an empty string.
        """
        suffix = f" [{default}]" if default else ""
        while True:
            raw = input(f"{prompt}{suffix}: ").strip()
            if raw:
                return raw
            if default is not None:
                return default
            if required:
                self.stderr.write(self.style.ERROR("This field is required."))
                continue
            return ""

    # ------------------------------------------------------------------
    # Channel selection
    # ------------------------------------------------------------------

    def _select_channel(
        self,
    ) -> tuple[str, dict[str, Any], str]:
        """Let the user pick an existing DB channel or configure a new one.

        Returns:
            (driver_name, config_dict, selected_label)
        """
        channels = list(NotificationChannel.objects.filter(is_active=True).order_by("name"))

        options: list[tuple[str, str]] = []
        for ch in channels:
            options.append((str(ch.pk), f"{ch.name} ({ch.driver})"))
        options.append(("__new__", "Configure a new driver manually"))

        choice = self._prompt_choice("Select a channel:", options)

        if choice == "__new__":
            return self._configure_new_driver()

        # DB-channel was selected
        channel = NotificationChannel.objects.get(pk=int(choice))
        return channel.driver, channel.config or {}, channel.name

    def _configure_new_driver(
        self,
    ) -> tuple[str, dict[str, Any], str]:
        """Prompt the user to pick a driver type and fill in its config.

        Returns:
            (driver_name, config_dict, selected_label)
        """
        driver_options: list[tuple[str, str]] = [(name, name) for name in DRIVER_REGISTRY]
        driver_name = self._prompt_choice("Select driver type:", driver_options)
        config = self._build_config_interactive(driver_name)
        return driver_name, config, f"new {driver_name}"

    def _build_config_interactive(self, driver_name: str) -> dict[str, Any]:
        """Collect driver-specific configuration fields interactively."""
        if driver_name == "slack":
            return {
                "webhook_url": self._prompt_input("Webhook URL", required=True),
            }
        elif driver_name == "email":
            return {
                "smtp_host": self._prompt_input("SMTP host", required=True),
                "from_address": self._prompt_input("From address", required=True),
                "smtp_port": self._prompt_input("SMTP port", default="587"),
            }
        elif driver_name == "pagerduty":
            return {
                "integration_key": self._prompt_input("Integration key", required=True),
            }
        elif driver_name == "generic":
            return {
                "endpoint": self._prompt_input("Endpoint URL", required=True),
            }
        return {}

    # ------------------------------------------------------------------
    # Message options
    # ------------------------------------------------------------------

    def _prompt_message_options(self, defaults: dict[str, str] | None = None) -> dict[str, str]:
        """Collect title, message, and severity from the user."""
        defaults = defaults or {}
        title = self._prompt_input(
            "Title",
            default=defaults.get("title", "Test Alert"),
        )
        message = self._prompt_input(
            "Message",
            default=defaults.get(
                "message",
                "This is a test notification from the notify app.",
            ),
        )
        severity = self._prompt_input(
            "Severity (critical/warning/info/success)",
            default=defaults.get("severity", "info"),
        )
        return {"title": title, "message": message, "severity": severity}

    # ------------------------------------------------------------------
    # Send and display
    # ------------------------------------------------------------------

    def _send_and_show_result(
        self,
        driver_name: str,
        config: dict[str, Any],
        selected_label: str,
        msg_opts: dict[str, str],
    ) -> dict[str, Any]:
        """Instantiate driver, validate, send, and display the result."""
        driver_class = DRIVER_REGISTRY.get(driver_name)
        if not driver_class:
            raise CommandError(
                f"Unknown driver: {driver_name}. " f"Available: {', '.join(DRIVER_REGISTRY.keys())}"
            )

        driver = driver_class()

        if not driver.validate_config(config):
            self.stderr.write(
                self.style.ERROR(
                    f"Invalid configuration for {driver_name}. " "Check required fields."
                )
            )
            return {"success": False, "error": "invalid config"}

        message = NotificationMessage(
            title=msg_opts["title"],
            message=msg_opts["message"],
            severity=msg_opts["severity"],
        )

        self.stdout.write(
            self.style.WARNING(
                f"Sending test notification via {selected_label} " f"(provider={driver_name})..."
            )
        )

        result = driver.send(message, config)

        success = bool(result.get("success") or result.get("status") == "success")
        if success:
            self.stdout.write(self.style.SUCCESS("Notification sent successfully!"))
            mid = result.get("message_id", "")
            if mid:
                self.stdout.write(f"  Message ID: {mid}")
            if result.get("metadata"):
                self.stdout.write(f"  Metadata: {json.dumps(result['metadata'], indent=2)}")
        else:
            self.stdout.write(self.style.ERROR("Failed to send notification"))
            self.stdout.write(f"  Error: {result.get('error') or result.get('message') or result}")

        return result

    # ------------------------------------------------------------------
    # Post-send loop
    # ------------------------------------------------------------------

    def _post_send_loop(self) -> str:
        """Ask the user what to do next after a send attempt.

        Returns one of: ``"retry"``, ``"switch"``, ``"done"``.
        """
        return self._prompt_choice(
            "What next?",
            [
                ("retry", "Retry with different message options"),
                ("switch", "Switch to a different channel"),
                ("done", "Done — exit"),
            ],
        )

    # ------------------------------------------------------------------
    # Interactive mode entry point
    # ------------------------------------------------------------------

    def _handle_interactive(self, **options: Any) -> None:
        """Run the interactive test-notification wizard."""
        self.stdout.write("=== Test Notification Wizard ===")

        driver_name, config, selected_label = self._select_channel()
        msg_opts = self._prompt_message_options()
        self._send_and_show_result(driver_name, config, selected_label, msg_opts)

        while True:
            action = self._post_send_loop()
            if action == "retry":
                msg_opts = self._prompt_message_options(defaults=msg_opts)
                self._send_and_show_result(driver_name, config, selected_label, msg_opts)
            elif action == "switch":
                driver_name, config, selected_label = self._select_channel()
                msg_opts = self._prompt_message_options(defaults=msg_opts)
                self._send_and_show_result(driver_name, config, selected_label, msg_opts)
            else:
                break
