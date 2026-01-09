"""
Management command to list available notification drivers and their requirements.

Usage:
    python manage.py list_notify_drivers
    python manage.py list_notify_drivers --verbose
"""

from django.core.management.base import BaseCommand

from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver

# Registry of available drivers
DRIVER_REGISTRY = {
    "email": {
        "class": EmailNotifyDriver,
        "description": "Send notifications via SMTP email",
        "required_config": ["smtp_host", "from_address"],
        "optional_config": ["smtp_port", "use_tls", "username", "password"],
    },
    "slack": {
        "class": SlackNotifyDriver,
        "description": "Send notifications to Slack via webhooks",
        "required_config": ["webhook_url"],
        "optional_config": ["channel"],
    },
    "pagerduty": {
        "class": PagerDutyNotifyDriver,
        "description": "Create incidents in PagerDuty",
        "required_config": ["integration_key"],
        "optional_config": ["api_version"],
    },
    "generic": {
        "class": GenericNotifyDriver,
        "description": "Send notifications to a custom HTTP endpoint",
        "required_config": [],
        "optional_config": ["endpoint", "webhook_url", "api_key", "headers"],
    },
}


class Command(BaseCommand):
    help = "List available notification drivers and their configuration requirements"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed configuration requirements",
        )

    def handle(self, *args, **options):
        verbose = options.get("verbose", False)

        self.stdout.write(self.style.SUCCESS("Available Notification Drivers"))
        self.stdout.write("-" * 60)

        for name, info in DRIVER_REGISTRY.items():
            self.stdout.write(f"\n{self.style.WARNING(name)}")
            self.stdout.write(f"  {info['description']}")

            if verbose:
                # Required config
                if info["required_config"]:
                    self.stdout.write("  Required config:")
                    for key in info["required_config"]:
                        self.stdout.write(f"    - {key}")
                else:
                    self.stdout.write("  Required config: none")

                # Optional config
                if info["optional_config"]:
                    self.stdout.write("  Optional config:")
                    for key in info["optional_config"]:
                        self.stdout.write(f"    - {key}")

        self.stdout.write("\n" + "-" * 60)
        self.stdout.write("\nUsage examples:")
        self.stdout.write(
            "  python manage.py test_notify email --smtp-host smtp.gmail.com --from-address alerts@example.com"
        )
        self.stdout.write(
            "  python manage.py test_notify slack --webhook-url https://hooks.slack.com/..."
        )
        self.stdout.write("  python manage.py test_notify pagerduty --integration-key xyz123")
        self.stdout.write(
            "  python manage.py test_notify generic --endpoint https://api.example.com/notify"
        )
