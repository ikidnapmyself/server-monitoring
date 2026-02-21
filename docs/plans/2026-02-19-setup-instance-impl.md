# Setup Instance Wizard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Django management command (`manage.py setup_instance`) that walks users through configuring their pipeline preset, stage drivers, and credentials — then writes `.env` updates and creates `PipelineDefinition` + `NotificationChannel` records.

**Architecture:** Single management command in `apps/orchestration` with step functions for each wizard phase. Input helpers (`_prompt_choice`, `_prompt_multi`, `_prompt_input`) wrap `input()` for consistent UX and testability. Config is applied atomically at the end.

**Tech Stack:** Django management commands, Django ORM (`PipelineDefinition`, `NotificationChannel`), Python `input()` for interactive prompts, `dotenv`-style `.env` file manipulation.

**Design doc:** `docs/plans/2026-02-19-setup-instance-design.md`

---

## Task 1: Input Helpers + Preset Selection

**Files:**
- Create: `apps/orchestration/management/commands/setup_instance.py`
- Create: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for input helpers and preset selection**

```python
# apps/orchestration/_tests/test_setup_instance.py
"""Tests for the setup_instance management command."""

from io import StringIO
from unittest.mock import patch

from django.test import TestCase

from apps.orchestration.management.commands.setup_instance import Command


class PromptChoiceTests(TestCase):
    """Tests for _prompt_choice helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="2")
    def test_returns_selected_option(self, _mock_input):
        result = self.cmd._prompt_choice(
            "Pick one:", [("a", "Option A"), ("b", "Option B"), ("c", "Option C")]
        )
        assert result == "b"

    @patch("builtins.input", side_effect=["0", "5", "2"])
    def test_retries_on_invalid_input(self, _mock_input):
        result = self.cmd._prompt_choice(
            "Pick one:", [("a", "Option A"), ("b", "Option B")]
        )
        assert result == "b"

    @patch("builtins.input", side_effect=["abc", "1"])
    def test_retries_on_non_numeric_input(self, _mock_input):
        result = self.cmd._prompt_choice(
            "Pick one:", [("a", "Option A")]
        )
        assert result == "a"


class PromptMultiTests(TestCase):
    """Tests for _prompt_multi helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,3")
    def test_returns_selected_options(self, _mock_input):
        result = self.cmd._prompt_multi(
            "Pick:", [("a", "A"), ("b", "B"), ("c", "C")]
        )
        assert result == ["a", "c"]

    @patch("builtins.input", return_value="1, 2, 3")
    def test_handles_spaces_in_input(self, _mock_input):
        result = self.cmd._prompt_multi(
            "Pick:", [("a", "A"), ("b", "B"), ("c", "C")]
        )
        assert result == ["a", "b", "c"]

    @patch("builtins.input", side_effect=["", "1"])
    def test_retries_on_empty_input(self, _mock_input):
        result = self.cmd._prompt_multi(
            "Pick:", [("a", "A"), ("b", "B")]
        )
        assert result == ["a"]


class PromptInputTests(TestCase):
    """Tests for _prompt_input helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="hello")
    def test_returns_user_input(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:")
        assert result == "hello"

    @patch("builtins.input", return_value="")
    def test_returns_default_when_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", default="fallback")
        assert result == "fallback"

    @patch("builtins.input", side_effect=["", "val"])
    def test_retries_when_required_and_empty(self, _mock_input):
        result = self.cmd._prompt_input("Enter value:", required=True)
        assert result == "val"


class SelectPresetTests(TestCase):
    """Tests for _select_preset step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_select_direct_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "direct"
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="4")
    def test_select_full_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "full"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is True

    @patch("builtins.input", return_value="2")
    def test_select_health_checked_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "health-checked"
        assert preset["has_checkers"] is True
        assert preset["has_intelligence"] is False

    @patch("builtins.input", return_value="3")
    def test_select_ai_analyzed_preset(self, _mock_input):
        preset = self.cmd._select_preset()
        assert preset["name"] == "ai-analyzed"
        assert preset["has_checkers"] is False
        assert preset["has_intelligence"] is True
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orchestration.management.commands.setup_instance'`

**Step 3: Implement input helpers and preset selection**

```python
# apps/orchestration/management/commands/setup_instance.py
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
        "label": "Alert → Notify",
        "description": "Direct forwarding",
        "has_checkers": False,
        "has_intelligence": False,
    },
    {
        "name": "health-checked",
        "label": "Alert → Checkers → Notify",
        "description": "Health-checked alerts",
        "has_checkers": True,
        "has_intelligence": False,
    },
    {
        "name": "ai-analyzed",
        "label": "Alert → Intelligence → Notify",
        "description": "AI-analyzed alerts",
        "has_checkers": False,
        "has_intelligence": True,
    },
    {
        "name": "full",
        "label": "Alert → Checkers → Intelligence → Notify",
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
        options = [
            (preset, f'{preset["label"]}  ({preset["description"]})')
            for preset in PRESETS
        ]
        selected = self._prompt_choice("? How will you use this instance?", options)
        return selected

    def handle(self, *args, **options):
        pass
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v`
Expected: All 13 tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add input helpers and preset selection"
```

---

## Task 2: Alert and Checker Stage Configuration

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for alert and checker configuration**

Add to `test_setup_instance.py`:

```python
class ConfigureAlertsTests(TestCase):
    """Tests for _configure_alerts step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,2,8")
    def test_returns_selected_drivers(self, _mock_input):
        result = self.cmd._configure_alerts()
        assert result == ["alertmanager", "grafana", "generic"]

    @patch("builtins.input", return_value="1")
    def test_single_driver_selection(self, _mock_input):
        result = self.cmd._configure_alerts()
        assert result == ["alertmanager"]


class ConfigureCheckersTests(TestCase):
    """Tests for _configure_checkers step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1,2")
    def test_returns_selected_checkers(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "cpu" in result["enabled"]
        assert "memory" in result["enabled"]

    @patch("builtins.input", side_effect=["1,2,3", "/,/home"])
    def test_disk_checker_asks_for_paths(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "disk" in result["enabled"]
        assert result["disk_paths"] == "/,/home"

    @patch("builtins.input", side_effect=["7", "8.8.8.8"])
    def test_network_checker_asks_for_hosts(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "network" in result["enabled"]
        assert result["network_hosts"] == "8.8.8.8"

    @patch("builtins.input", side_effect=["8", "nginx,postgres"])
    def test_process_checker_asks_for_names(self, _mock_input):
        result = self.cmd._configure_checkers()
        assert "process" in result["enabled"]
        assert result["process_names"] == "nginx,postgres"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py::ConfigureAlertsTests -v`
Expected: FAIL — `AttributeError: 'Command' object has no attribute '_configure_alerts'`

**Step 3: Implement alert and checker configuration**

Add to `setup_instance.py` Command class:

```python
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
        selected = self._prompt_multi(
            "? Which health checkers do you want to enable?", options
        )

        result = {"enabled": selected}

        if "disk" in selected:
            result["disk_paths"] = self._prompt_input(
                "  Disk paths to monitor", default="/"
            )
        if "network" in selected:
            result["network_hosts"] = self._prompt_input(
                "  Hosts to ping", default="8.8.8.8,1.1.1.1"
            )
        if "process" in selected:
            result["process_names"] = self._prompt_input(
                "  Process names to watch", required=True
            )

        return result
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "Alerts or Checkers"`
Expected: All 6 new tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add alert and checker stage configuration"
```

---

## Task 3: Intelligence and Notify Stage Configuration

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for intelligence and notify configuration**

Add to `test_setup_instance.py`:

```python
class ConfigureIntelligenceTests(TestCase):
    """Tests for _configure_intelligence step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_local_provider_needs_no_config(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["provider"] == "local"
        assert "api_key" not in result

    @patch("builtins.input", side_effect=["2", "sk-test123", "gpt-4o-mini"])
    def test_openai_provider_collects_credentials(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["provider"] == "openai"
        assert result["api_key"] == "sk-test123"
        assert result["model"] == "gpt-4o-mini"

    @patch("builtins.input", side_effect=["2", "sk-test123", ""])
    def test_openai_uses_default_model(self, _mock_input):
        result = self.cmd._configure_intelligence()
        assert result["model"] == "gpt-4o-mini"


class ConfigureNotifyTests(TestCase):
    """Tests for _configure_notify step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch(
        "builtins.input",
        side_effect=["2", "https://hooks.slack.com/xxx", "ops-alerts"],
    )
    def test_slack_collects_webhook_url_and_name(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "slack"
        assert result[0]["config"]["webhook_url"] == "https://hooks.slack.com/xxx"
        assert result[0]["name"] == "ops-alerts"

    @patch(
        "builtins.input",
        side_effect=[
            "1",  # email
            "smtp.example.com",  # host
            "587",  # port
            "user@example.com",  # user
            "password123",  # password
            "noreply@example.com",  # from
            "ops@example.com",  # to
            "ops-email",  # channel name
        ],
    )
    def test_email_collects_smtp_settings(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "email"
        assert result[0]["config"]["smtp_host"] == "smtp.example.com"
        assert result[0]["config"]["smtp_port"] == "587"
        assert result[0]["name"] == "ops-email"

    @patch(
        "builtins.input",
        side_effect=["3", "R0123456789", "oncall-pd"],
    )
    def test_pagerduty_collects_routing_key(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "pagerduty"
        assert result[0]["config"]["routing_key"] == "R0123456789"

    @patch(
        "builtins.input",
        side_effect=["4", "https://example.com/hook", "", "my-webhook"],
    )
    def test_generic_collects_endpoint(self, _mock_input):
        result = self.cmd._configure_notify()
        assert len(result) == 1
        assert result[0]["driver"] == "generic"
        assert result[0]["config"]["endpoint_url"] == "https://example.com/hook"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "Intelligence or Notify"`
Expected: FAIL — `AttributeError`

**Step 3: Implement intelligence and notify configuration**

Add to `setup_instance.py` Command class:

```python
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
                config["smtp_password"] = self._prompt_input(
                    "    SMTP password", required=True
                )
                config["smtp_from"] = self._prompt_input("    From address", required=True)
                config["smtp_to"] = self._prompt_input("    To address", required=True)
            elif driver_name == "slack":
                config["webhook_url"] = self._prompt_input(
                    "    Slack webhook URL", required=True
                )
            elif driver_name == "pagerduty":
                config["routing_key"] = self._prompt_input(
                    "    PagerDuty routing key", required=True
                )
            elif driver_name == "generic":
                config["endpoint_url"] = self._prompt_input(
                    "    Endpoint URL", required=True
                )
                headers = self._prompt_input("    Headers (JSON, optional)", default="")
                if headers:
                    config["headers"] = headers

            channel_name = self._prompt_input(
                f"    Channel name", default=f"ops-{driver_name}"
            )
            channels.append({"driver": driver_name, "name": channel_name, "config": config})

        return channels
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "Intelligence or Notify"`
Expected: All 7 new tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add intelligence and notify stage configuration"
```

---

## Task 4: Summary Display and Confirmation

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for summary and confirmation**

Add to `test_setup_instance.py`:

```python
class ShowSummaryTests(TestCase):
    """Tests for _show_summary step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_summary_includes_preset_name(self):
        config = {
            "preset": {"name": "full", "label": "Full pipeline"},
            "alerts": ["alertmanager", "grafana"],
            "checkers": {"enabled": ["cpu", "memory"]},
            "intelligence": {"provider": "openai", "model": "gpt-4o-mini"},
            "notify": [{"driver": "slack", "name": "ops-alerts"}],
        }
        self.cmd._show_summary(config)
        output = self.cmd.stdout.getvalue()
        assert "full" in output.lower() or "Full pipeline" in output

    def test_summary_includes_all_drivers(self):
        config = {
            "preset": {"name": "direct", "label": "Direct"},
            "alerts": ["grafana"],
            "notify": [{"driver": "slack", "name": "ops-slack"}],
        }
        self.cmd._show_summary(config)
        output = self.cmd.stdout.getvalue()
        assert "grafana" in output
        assert "slack" in output


class ConfirmApplyTests(TestCase):
    """Tests for _confirm_apply step."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="Y")
    def test_returns_true_on_yes(self, _mock_input):
        assert self.cmd._confirm_apply() is True

    @patch("builtins.input", return_value="")
    def test_returns_true_on_empty_default_yes(self, _mock_input):
        assert self.cmd._confirm_apply() is True

    @patch("builtins.input", return_value="n")
    def test_returns_false_on_no(self, _mock_input):
        assert self.cmd._confirm_apply() is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "Summary or Confirm"`
Expected: FAIL — `AttributeError`

**Step 3: Implement summary and confirmation**

Add to `setup_instance.py` Command class:

```python
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
            self.stdout.write(
                f"  Checkers: {', '.join(config['checkers']['enabled'])}"
            )

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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "Summary or Confirm"`
Expected: All 5 new tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add summary display and confirmation"
```

---

## Task 5: .env Writer

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for .env writing**

Add to `test_setup_instance.py`:

```python
import os
import tempfile


class WriteEnvTests(TestCase):
    """Tests for _write_env helper."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_adds_new_keys_to_env_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("EXISTING_KEY=value\n")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"NEW_KEY": "new_value"})
            with open(env_path) as f:
                content = f.read()
            assert "EXISTING_KEY=value" in content
            assert "NEW_KEY=new_value" in content
        finally:
            os.unlink(env_path)

    def test_updates_existing_keys(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("MY_KEY=old\nOTHER=keep\n")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"MY_KEY": "new"})
            with open(env_path) as f:
                content = f.read()
            assert "MY_KEY=new" in content
            assert "MY_KEY=old" not in content
            assert "OTHER=keep" in content
        finally:
            os.unlink(env_path)

    def test_creates_env_file_if_missing(self):
        env_path = tempfile.mktemp(suffix=".env")
        try:
            self.cmd._write_env(env_path, {"KEY": "val"})
            with open(env_path) as f:
                content = f.read()
            assert "KEY=val" in content
        finally:
            if os.path.exists(env_path):
                os.unlink(env_path)

    def test_adds_section_header_comment(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("")
            f.flush()
            env_path = f.name

        try:
            self.cmd._write_env(env_path, {"KEY": "val"})
            with open(env_path) as f:
                content = f.read()
            assert "setup_instance" in content
        finally:
            os.unlink(env_path)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py::WriteEnvTests -v`
Expected: FAIL — `AttributeError: 'Command' object has no attribute '_write_env'`

**Step 3: Implement .env writer**

Add to `setup_instance.py` Command class:

```python
    def _write_env(self, env_path, updates):
        """
        Update .env file with new key-value pairs, preserving existing content.

        Args:
            env_path: Path to .env file.
            updates: Dict of key-value pairs to set.
        """
        import datetime

        lines = []
        existing_keys = set()

        # Read existing file
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    stripped = line.strip()
                    # Check if this line sets a key we're updating
                    if "=" in stripped and not stripped.startswith("#"):
                        key = stripped.split("=", 1)[0].strip()
                        if key in updates:
                            lines.append(f"{key}={updates[key]}\n")
                            existing_keys.add(key)
                            continue
                    lines.append(line)

        # Append new keys that weren't already in the file
        new_keys = {k: v for k, v in updates.items() if k not in existing_keys}
        if new_keys:
            today = datetime.date.today().isoformat()
            lines.append(f"\n# --- setup_instance: Generated {today} ---\n")
            for key, value in new_keys.items():
                lines.append(f"{key}={value}\n")

        with open(env_path, "w") as f:
            f.writelines(lines)
```

Also add `import os` at the top of the file if not already present.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py::WriteEnvTests -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add .env file writer"
```

---

## Task 6: PipelineDefinition and NotificationChannel Creation

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for DB record creation**

Add to `test_setup_instance.py`:

```python
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class CreatePipelineDefinitionTests(TestCase):
    """Tests for _create_pipeline_definition."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_creates_direct_pipeline(self):
        config = {
            "preset": {"name": "direct", "has_checkers": False, "has_intelligence": False},
            "alerts": ["grafana"],
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        assert defn.name == "direct"
        assert defn.is_active is True
        assert "setup_wizard" in defn.tags
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert "ingest" in node_types
        assert "notify" in node_types
        assert "context" not in node_types
        assert "intelligence" not in node_types

    def test_creates_full_pipeline(self):
        config = {
            "preset": {"name": "full", "has_checkers": True, "has_intelligence": True},
            "alerts": ["alertmanager"],
            "checkers": {"enabled": ["cpu", "memory"]},
            "intelligence": {"provider": "openai"},
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

    def test_nodes_are_chained_with_next(self):
        config = {
            "preset": {"name": "full", "has_checkers": True, "has_intelligence": True},
            "alerts": ["alertmanager"],
            "checkers": {"enabled": ["cpu"]},
            "intelligence": {"provider": "local"},
            "notify": [{"driver": "slack", "name": "ops-slack", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        nodes = defn.get_nodes()
        # Each node except last should have "next" pointing to next node
        for i, node in enumerate(nodes[:-1]):
            assert node["next"] == nodes[i + 1]["id"]
        assert "next" not in nodes[-1]

    def test_tags_include_setup_wizard(self):
        config = {
            "preset": {"name": "direct", "has_checkers": False, "has_intelligence": False},
            "alerts": ["generic"],
            "notify": [{"driver": "generic", "name": "wh", "config": {}}],
        }
        defn = self.cmd._create_pipeline_definition(config)
        assert "setup_wizard" in defn.tags


class CreateNotificationChannelsTests(TestCase):
    """Tests for _create_notification_channels."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_creates_channel_records(self):
        channels_config = [
            {
                "driver": "slack",
                "name": "ops-slack",
                "config": {"webhook_url": "https://hooks.slack.com/xxx"},
            },
        ]
        channels = self.cmd._create_notification_channels(channels_config)
        assert len(channels) == 1
        ch = NotificationChannel.objects.get(name="ops-slack")
        assert ch.driver == "slack"
        assert ch.config["webhook_url"] == "https://hooks.slack.com/xxx"
        assert ch.is_active is True
        assert "[setup_wizard]" in ch.description

    def test_creates_multiple_channels(self):
        channels_config = [
            {"driver": "slack", "name": "slack-ch", "config": {}},
            {"driver": "email", "name": "email-ch", "config": {}},
        ]
        channels = self.cmd._create_notification_channels(channels_config)
        assert len(channels) == 2
        assert NotificationChannel.objects.count() == 2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "CreatePipeline or CreateNotification"`
Expected: FAIL — `AttributeError`

**Step 3: Implement DB record creation**

Add to `setup_instance.py` Command class:

```python
    def _create_pipeline_definition(self, config):
        """
        Create a PipelineDefinition record from collected config.

        Args:
            config: Full wizard config dict.

        Returns:
            Created PipelineDefinition instance.
        """
        from apps.orchestration.models import PipelineDefinition

        preset = config["preset"]
        nodes = []

        # Build node chain based on preset
        node_defs = [("ingest_webhook", "ingest", {})]

        if preset["has_checkers"]:
            checker_config = config.get("checkers", {})
            node_defs.append((
                "check_health",
                "context",
                {"checker_names": checker_config.get("enabled", [])},
            ))

        if preset["has_intelligence"]:
            intel_config = config.get("intelligence", {})
            node_defs.append((
                "analyze_incident",
                "intelligence",
                {"provider": intel_config.get("provider", "local")},
            ))

        notify_drivers = [ch["driver"] for ch in config.get("notify", [])]
        node_defs.append((
            "notify_channels",
            "notify",
            {"drivers": notify_drivers},
        ))

        # Chain nodes with "next" pointers
        for i, (node_id, node_type, node_config) in enumerate(node_defs):
            node = {"id": node_id, "type": node_type, "config": node_config}
            if i < len(node_defs) - 1:
                node["next"] = node_defs[i + 1][0]
            nodes.append(node)

        pipeline_config = {
            "version": "1.0",
            "description": f"Setup wizard: {preset['name']}",
            "defaults": {"max_retries": 3, "timeout_seconds": 300},
            "nodes": nodes,
        }

        return PipelineDefinition.objects.create(
            name=preset["name"],
            description=f"Pipeline created by setup_instance wizard ({preset['name']})",
            config=pipeline_config,
            tags=["setup_wizard"],
            created_by="setup_instance",
        )

    def _create_notification_channels(self, channels_config):
        """
        Create NotificationChannel records from collected config.

        Args:
            channels_config: List of dicts with 'driver', 'name', 'config'.

        Returns:
            List of created NotificationChannel instances.
        """
        from apps.notify.models import NotificationChannel

        created = []
        for ch in channels_config:
            channel = NotificationChannel.objects.create(
                name=ch["name"],
                driver=ch["driver"],
                config=ch["config"],
                is_active=True,
                description=f"[setup_wizard] {ch['driver']} channel",
            )
            created.append(channel)
        return created
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "CreatePipeline or CreateNotification"`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add PipelineDefinition and NotificationChannel creation"
```

---

## Task 7: Re-run Detection

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing tests for re-run detection**

Add to `test_setup_instance.py`:

```python
class DetectExistingTests(TestCase):
    """Tests for _detect_existing."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    def test_returns_none_when_no_existing(self):
        result = self.cmd._detect_existing()
        assert result is None

    def test_returns_definition_when_exists(self):
        PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        result = self.cmd._detect_existing()
        assert result is not None
        assert result.name == "full"

    def test_ignores_non_wizard_definitions(self):
        PipelineDefinition.objects.create(
            name="custom",
            config={"version": "1.0", "nodes": []},
            tags=["manual"],
            created_by="admin",
        )
        result = self.cmd._detect_existing()
        assert result is None


class HandleRerunTests(TestCase):
    """Tests for _handle_rerun."""

    def setUp(self):
        self.cmd = Command(stdout=StringIO(), stderr=StringIO())

    @patch("builtins.input", return_value="1")
    def test_reconfigure_deactivates_existing(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={},
            description="[setup_wizard] slack channel",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "reconfigure"
        defn.refresh_from_db()
        assert defn.is_active is False
        ch = NotificationChannel.objects.get(name="ops-slack")
        assert ch.is_active is False

    @patch("builtins.input", return_value="2")
    def test_add_another_keeps_existing(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "add"
        defn.refresh_from_db()
        assert defn.is_active is True

    @patch("builtins.input", return_value="3")
    def test_cancel_returns_cancel(self, _mock_input):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={"version": "1.0", "nodes": []},
            tags=["setup_wizard"],
            created_by="setup_instance",
        )
        action = self.cmd._handle_rerun(defn)
        assert action == "cancel"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "DetectExisting or HandleRerun"`
Expected: FAIL — `AttributeError`

**Step 3: Implement re-run detection**

Add to `setup_instance.py` Command class:

```python
    def _detect_existing(self):
        """
        Detect existing wizard-created pipeline definition.

        Returns:
            PipelineDefinition instance if found, None otherwise.
        """
        from apps.orchestration.models import PipelineDefinition

        return (
            PipelineDefinition.objects.filter(tags__contains="setup_wizard", is_active=True)
            .order_by("-updated_at")
            .first()
        )

    def _handle_rerun(self, existing):
        """
        Handle re-run when existing wizard config is detected.

        Args:
            existing: Existing PipelineDefinition instance.

        Returns:
            Action string: 'reconfigure', 'add', or 'cancel'.
        """
        from apps.notify.models import NotificationChannel

        action = self._prompt_choice(
            f'? Existing pipeline "{existing.name}" found. What would you like to do?',
            [
                ("reconfigure", "Reconfigure — Replace existing pipeline and channels"),
                ("add", "Add another — Create additional pipeline alongside existing"),
                ("cancel", "Cancel"),
            ],
        )

        if action == "reconfigure":
            existing.is_active = False
            existing.save(update_fields=["is_active"])
            NotificationChannel.objects.filter(
                description__startswith="[setup_wizard]", is_active=True
            ).update(is_active=False)

        return action
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v -k "DetectExisting or HandleRerun"`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): add re-run detection and handling"
```

---

## Task 8: Wire Up handle() — Full Flow

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py`
- Modify: `apps/orchestration/_tests/test_setup_instance.py`

**Step 1: Write failing integration test**

Add to `test_setup_instance.py`:

```python
from django.core.management import call_command


class SetupInstanceIntegrationTests(TestCase):
    """Integration tests for the full setup_instance flow."""

    @patch(
        "builtins.input",
        side_effect=[
            "4",  # preset: full
            "1,2",  # alerts: alertmanager, grafana
            "1,2",  # checkers: cpu, memory
            "1",  # intelligence: local
            "2",  # notify: slack (2nd in registry: pagerduty=1? No — slack=1)
            "https://hooks.slack.com/xxx",  # slack webhook
            "ops-alerts",  # channel name
            "Y",  # confirm
        ],
    )
    def test_full_pipeline_flow(self, _mock_input):
        out = StringIO()
        call_command("setup_instance", stdout=out)

        # Verify PipelineDefinition created
        defn = PipelineDefinition.objects.get(tags__contains="setup_wizard")
        assert defn.is_active is True
        nodes = defn.get_nodes()
        node_types = [n["type"] for n in nodes]
        assert node_types == ["ingest", "context", "intelligence", "notify"]

        # Verify NotificationChannel created
        ch = NotificationChannel.objects.get(name="ops-alerts")
        assert ch.driver == "slack"
        assert ch.is_active is True

    @patch(
        "builtins.input",
        side_effect=[
            "1",  # preset: direct
            "1",  # alerts: alertmanager (or first)
            "1",  # notify: first driver
            "https://hooks.slack.com/xxx",  # config
            "ops-slack",  # channel name
            "Y",  # confirm
        ],
    )
    def test_direct_preset_skips_checkers_and_intelligence(self, _mock_input):
        out = StringIO()
        call_command("setup_instance", stdout=out)

        defn = PipelineDefinition.objects.get(tags__contains="setup_wizard")
        node_types = [n["type"] for n in defn.get_nodes()]
        assert "context" not in node_types
        assert "intelligence" not in node_types

    @patch("builtins.input", side_effect=["n"])
    def test_cancel_on_confirmation_creates_nothing(self, _mock_input):
        """When user cancels at confirmation, no DB records should be created."""
        out = StringIO()
        # This will need enough inputs to get to confirmation
        with patch(
            "builtins.input",
            side_effect=[
                "1",  # preset: direct
                "1",  # alerts
                "1",  # notify
                "https://example.com",  # config
                "test-ch",  # name
                "n",  # cancel
            ],
        ):
            call_command("setup_instance", stdout=out)
        assert PipelineDefinition.objects.count() == 0
        assert NotificationChannel.objects.count() == 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py::SetupInstanceIntegrationTests -v`
Expected: FAIL — `handle()` does nothing yet

**Step 3: Implement the handle() method**

Replace the `handle` method in `setup_instance.py`:

```python
    def handle(self, *args, **options):
        from django.conf import settings

        self.stdout.write(self.style.HTTP_INFO(
            "\n╔══════════════════════════════════════════════════╗"
            "\n║     Server Maintenance — Instance Setup          ║"
            "\n╚══════════════════════════════════════════════════╝"
        ))

        # Check for existing wizard configuration
        existing = self._detect_existing()
        rerun_action = None
        if existing:
            rerun_action = self._handle_rerun(existing)
            if rerun_action == "cancel":
                self.stdout.write("Setup cancelled.")
                return

        # Step 1: Select pipeline preset
        preset = self._select_preset()

        # Step 2: Configure alerts (always present)
        alerts = self._configure_alerts()

        # Step 3: Configure checkers (if preset includes them)
        checkers = None
        if preset["has_checkers"]:
            checkers = self._configure_checkers()

        # Step 4: Configure intelligence (if preset includes it)
        intelligence = None
        if preset["has_intelligence"]:
            intelligence = self._configure_intelligence()

        # Step 5: Configure notifications (always present)
        notify = self._configure_notify()

        # Build full config
        config = {"preset": preset, "alerts": alerts, "notify": notify}
        if checkers:
            config["checkers"] = checkers
        if intelligence:
            config["intelligence"] = intelligence

        # Step 6: Show summary and confirm
        self._show_summary(config)
        if not self._confirm_apply():
            self.stdout.write("Setup cancelled.")
            return

        # Step 7: Apply configuration
        env_path = os.path.join(str(settings.BASE_DIR), ".env")
        env_updates = {}

        env_updates["ALERTS_ENABLED_DRIVERS"] = ",".join(alerts)

        if checkers:
            from apps.checkers.checkers import CHECKER_REGISTRY

            all_checkers = set(CHECKER_REGISTRY.keys())
            enabled = set(checkers["enabled"])
            skipped = all_checkers - enabled
            if skipped:
                env_updates["CHECKERS_SKIP"] = ",".join(sorted(skipped))

        if intelligence:
            env_updates["INTELLIGENCE_PROVIDER"] = intelligence["provider"]
            if intelligence.get("api_key"):
                env_updates["OPENAI_API_KEY"] = intelligence["api_key"]
            if intelligence.get("model"):
                env_updates["OPENAI_MODEL"] = intelligence["model"]

        self._write_env(env_path, env_updates)
        self.stdout.write(
            self.style.SUCCESS(f"✓ Updated .env with {len(env_updates)} setting(s)")
        )

        # Handle name collision for "add another" mode
        pipeline_name = preset["name"]
        if rerun_action == "add":
            from apps.orchestration.models import PipelineDefinition

            count = PipelineDefinition.objects.filter(
                name__startswith=pipeline_name
            ).count()
            if count > 0:
                pipeline_name = f"{pipeline_name}-{count + 1}"
            config["preset"] = {**preset, "name": pipeline_name}

        defn = self._create_pipeline_definition(config)
        self.stdout.write(
            self.style.SUCCESS(f'✓ Created PipelineDefinition "{defn.name}"')
        )

        channels = self._create_notification_channels(notify)
        for ch in channels:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Created NotificationChannel "{ch.name}" ({ch.driver})')
            )

        self.stdout.write(self.style.SUCCESS("\n✓ Configuration complete!"))
        self.stdout.write(
            "\nNext steps:\n"
            "  uv run python manage.py run_pipeline --sample --dry-run\n"
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v`
Expected: ALL tests PASS

**Step 5: Run all project tests for regression**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass, no regressions

**Step 6: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "feat(setup_instance): wire up full wizard flow in handle()"
```

---

## Task 9: Final Polish and Full Test Run

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py` (if needed)
- Modify: `apps/orchestration/_tests/test_setup_instance.py` (if needed)

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass

**Step 2: Run code quality checks**

Run: `uv run black . && uv run ruff check . --fix && uv run mypy apps/orchestration/management/commands/setup_instance.py`
Expected: All pass clean

**Step 3: Manual smoke test (optional)**

Run: `uv run python manage.py setup_instance`
Walk through the wizard manually to verify UX.

**Step 4: Final commit if any fixes needed**

```bash
git add -u
git commit -m "chore(setup_instance): polish and lint fixes"
```

---

## Summary

| Task | What | Tests |
|------|------|-------|
| 1 | Input helpers + preset selection | 13 |
| 2 | Alert + checker config | 6 |
| 3 | Intelligence + notify config | 7 |
| 4 | Summary + confirmation | 5 |
| 5 | .env writer | 4 |
| 6 | PipelineDefinition + NotificationChannel creation | 6 |
| 7 | Re-run detection | 6 |
| 8 | Full handle() integration | 3 |
| 9 | Polish + full test run | — |
| **Total** | | **~50** |