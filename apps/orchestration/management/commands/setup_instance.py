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
