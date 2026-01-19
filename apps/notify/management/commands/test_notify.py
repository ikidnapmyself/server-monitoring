"""
Management command to test notification delivery to various backends.

Usage:
    python manage.py test_notify email
    python manage.py test_notify slack --webhook-url https://hooks.slack.com/...
    python manage.py test_notify pagerduty --integration-key xyz123
    python manage.py test_notify email --smtp-host smtp.gmail.com --from-address alerts@example.com
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver

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
        # Make driver optional: when omitted, pick first active NotificationChannel from DB
        parser.add_argument(
            "driver",
            nargs="?",
            default=None,
            type=str,
            help=f"Notification driver or channel name to test. Options: {', '.join(DRIVER_REGISTRY.keys())} (optional, will use first active DB channel if omitted)",
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
