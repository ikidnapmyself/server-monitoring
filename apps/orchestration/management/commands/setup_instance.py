"""
Interactive setup wizard for configuring a server-maintanence instance.

Guides the user through selecting a pipeline preset, configuring drivers
per active stage, collecting credentials, and writing configuration.

Usage:
    python manage.py setup_instance
"""

from django.core.management.base import BaseCommand

# Preset definitions: name, label, description, active stages
PRESETS = [
    {
        "name": "direct",
        "label": "Alert \u2192 Notify",
        "description": "Direct forwarding",
        "has_checkers": False,
        "has_intelligence": False,
    },
    {
        "name": "health-checked",
        "label": "Alert \u2192 Checkers \u2192 Notify",
        "description": "Health-checked alerts",
        "has_checkers": True,
        "has_intelligence": False,
    },
    {
        "name": "ai-analyzed",
        "label": "Alert \u2192 Intelligence \u2192 Notify",
        "description": "AI-analyzed alerts",
        "has_checkers": False,
        "has_intelligence": True,
    },
    {
        "name": "full",
        "label": "Alert \u2192 Checkers \u2192 Intelligence \u2192 Notify",
        "description": "Full pipeline",
        "has_checkers": True,
        "has_intelligence": True,
    },
]


class Command(BaseCommand):
    help = "Interactive setup wizard for configuring your server-maintanence instance."

    def _prompt_choice(self, prompt, options):
        """
        Prompt user to select one option from a numbered list.

        Args:
            prompt: Question text to display.
            options: List of (value, label) tuples.

        Returns:
            The value of the selected option.
        """
        self.stdout.write(f"\n{prompt}")
        for i, (_, label) in enumerate(options, 1):
            self.stdout.write(f"  {i}) {label}")

        while True:
            try:
                choice = int(input("\n> "))
                if 1 <= choice <= len(options):
                    return options[choice - 1][0]
            except (ValueError, IndexError):
                pass
            self.stdout.write(self.style.WARNING(f"  Please enter 1-{len(options)}."))

    def _prompt_multi(self, prompt, options):
        """
        Prompt user to select one or more options (comma-separated numbers).

        Args:
            prompt: Question text to display.
            options: List of (value, label) tuples.

        Returns:
            List of selected values.
        """
        self.stdout.write(f"\n{prompt}")
        for i, (_, label) in enumerate(options, 1):
            self.stdout.write(f"  {i}) {label}")

        while True:
            raw = input("\n> (comma-separated, e.g. 1,3): ")
            try:
                indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
                if indices and all(1 <= i <= len(options) for i in indices):
                    return [options[i - 1][0] for i in indices]
            except (ValueError, IndexError):
                pass
            self.stdout.write(
                self.style.WARNING(f"  Enter comma-separated numbers 1-{len(options)}.")
            )

    def _prompt_input(self, prompt, default=None, required=False):
        """
        Prompt user for free-text input.

        Args:
            prompt: Question text.
            default: Default value if user presses Enter.
            required: If True, retry on empty input.

        Returns:
            User input string, or default.
        """
        suffix = f" [{default}]" if default else ""
        while True:
            value = input(f"{prompt}{suffix}: ").strip()
            if value:
                return value
            if default is not None:
                return default
            if required:
                self.stdout.write(self.style.WARNING("  Value cannot be empty."))
                continue
            return ""

    def _configure_alerts(self):
        """
        Prompt user to select alert drivers.

        Returns:
            List of selected driver name strings.
        """
        from apps.alerts.drivers import DRIVER_REGISTRY

        self.stdout.write(self.style.HTTP_INFO("\n--- Stage: Alerts ---"))
        options = [(name, name) for name in DRIVER_REGISTRY]
        return self._prompt_multi("? Which alert drivers do you want to enable?", options)

    def _configure_checkers(self):
        """
        Prompt user to select health checkers and per-checker config.

        Returns:
            Dict with 'enabled' list and optional per-checker config keys:
            disk_paths, network_hosts, process_names.
        """
        from apps.checkers.checkers import CHECKER_REGISTRY

        self.stdout.write(self.style.HTTP_INFO("\n--- Stage: Checkers ---"))
        options = [(name, name) for name in CHECKER_REGISTRY]
        selected = self._prompt_multi("? Which health checkers do you want to enable?", options)

        result = {"enabled": selected}

        if "disk" in selected:
            result["disk_paths"] = self._prompt_input("  Disk paths to monitor", default="/")
        if "network" in selected:
            result["network_hosts"] = self._prompt_input(
                "  Hosts to ping", default="8.8.8.8,1.1.1.1"
            )
        if "process" in selected:
            result["process_names"] = self._prompt_input("  Process names to watch", required=True)

        return result

    def _configure_intelligence(self):
        """
        Prompt user to select an AI provider and collect credentials.

        Returns:
            Dict with 'provider' and optional 'api_key', 'model'.
        """
        from apps.intelligence.providers import PROVIDERS

        self.stdout.write(self.style.HTTP_INFO("\n--- Stage: Intelligence ---"))
        options = [(name, name) for name in PROVIDERS]
        provider = self._prompt_choice("? Which AI provider do you want to use?", options)

        result = {"provider": provider}

        if provider == "openai":
            result["api_key"] = self._prompt_input("  OpenAI API key", required=True)
            result["model"] = self._prompt_input("  OpenAI model", default="gpt-4o-mini")

        return result

    def _configure_notify(self):
        """
        Prompt user to select notification channels and collect per-driver config.

        Returns:
            List of dicts, each with 'driver', 'name', 'config'.
        """
        from apps.notify.drivers import DRIVER_REGISTRY

        self.stdout.write(self.style.HTTP_INFO("\n--- Stage: Notify ---"))
        options = [(name, name) for name in DRIVER_REGISTRY]
        selected = self._prompt_multi(
            "? Which notification channels do you want to configure?", options
        )

        channels = []
        for driver_name in selected:
            self.stdout.write(f"\n  Configuring {driver_name}:")
            config = {}

            if driver_name == "email":
                config["smtp_host"] = self._prompt_input("    SMTP host", required=True)
                config["smtp_port"] = self._prompt_input("    SMTP port", default="587")
                config["smtp_user"] = self._prompt_input("    SMTP user", required=True)
                config["smtp_password"] = self._prompt_input("    SMTP password", required=True)
                config["smtp_from"] = self._prompt_input("    From address", required=True)
                config["smtp_to"] = self._prompt_input("    To address", required=True)
            elif driver_name == "slack":
                config["webhook_url"] = self._prompt_input("    Slack webhook URL", required=True)
            elif driver_name == "pagerduty":
                config["routing_key"] = self._prompt_input(
                    "    PagerDuty routing key", required=True
                )
            elif driver_name == "generic":
                config["endpoint_url"] = self._prompt_input("    Endpoint URL", required=True)
                headers = self._prompt_input("    Headers (JSON, optional)", default="")
                if headers:
                    config["headers"] = headers

            channel_name = self._prompt_input("    Channel name", default=f"ops-{driver_name}")
            channels.append({"driver": driver_name, "name": channel_name, "config": config})

        return channels

    def _show_summary(self, config):
        """
        Display a summary of collected configuration for user review.

        Args:
            config: Dict with all collected wizard state.
        """
        self.stdout.write(self.style.HTTP_INFO("\n--- Summary ---"))
        self.stdout.write(f"  Pipeline: {config['preset']['label']}")

        if "alerts" in config:
            self.stdout.write(f"  Alert drivers: {', '.join(config['alerts'])}")

        if "checkers" in config:
            self.stdout.write(f"  Checkers: {', '.join(config['checkers']['enabled'])}")

        if "intelligence" in config:
            intel = config["intelligence"]
            provider_info = intel["provider"]
            if intel.get("model"):
                provider_info += f" ({intel['model']})"
            self.stdout.write(f"  Intelligence: {provider_info}")

        if "notify" in config:
            for ch in config["notify"]:
                self.stdout.write(f"  Notification: {ch['driver']} ({ch['name']})")

    def _confirm_apply(self):
        """
        Ask user to confirm applying configuration.

        Returns:
            True if user confirms, False otherwise.
        """
        response = input("\n? Apply this configuration? [Y/n]: ").strip().lower()
        return response in ("", "y", "yes")

    def _select_preset(self):
        """
        Prompt user to select a pipeline preset.

        Returns:
            Dict with preset metadata (name, has_checkers, has_intelligence).
        """
        options = [(preset, f'{preset["label"]}  ({preset["description"]})') for preset in PRESETS]
        selected = self._prompt_choice("? How will you use this instance?", options)
        return selected

    def handle(self, *args, **options):
        pass
