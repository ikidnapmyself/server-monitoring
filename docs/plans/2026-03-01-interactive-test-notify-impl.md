---
title: "Interactive test_notify Implementation Plan"
parent: Plans
nav_order: 79739698
---
# Interactive test_notify Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an interactive wizard to `test_notify` that discovers channels, guides config, and supports adjust-and-retry — with `--non-interactive` flag to preserve current behavior.

**Architecture:** Overlay wizard on existing command. `handle()` routes to `_handle_interactive()` (default) or `_handle_non_interactive()` (flag). Wizard reuses existing `_build_*_config` helpers and `NotifySelector.resolve()`. Interactive prompts follow the same `_prompt_choice` / `_prompt_input` pattern used in `setup_instance`.

**Tech Stack:** Django management commands, `input()` for prompts, `unittest.mock.patch("builtins.input")` for testing.

---

### Task 1: Rename existing handle() to _handle_non_interactive and add routing

**Files:**
- Modify: `apps/notify/management/commands/test_notify.py:123-202`
- Test: `apps/notify/_tests/test_test_notify.py` (create)

**Step 1: Write failing tests for non-interactive mode**

Create `apps/notify/_tests/test_test_notify.py`:

```python
"""Tests for the test_notify management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from apps.notify.models import NotificationChannel


class NonInteractiveTests(TestCase):
    """Tests for --non-interactive flag preserving existing behavior."""

    def test_non_interactive_with_db_channel_sends(self):
        """--non-interactive with a named DB channel sends via that channel."""
        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
            description="[setup_wizard] slack channel",
        )
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "test-123", "metadata": {}},
        ):
            call_command(
                "test_notify",
                "ops-slack",
                "--non-interactive",
                stdout=out,
            )
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_unknown_driver_raises(self):
        """--non-interactive with unknown driver raises CommandError."""
        out = StringIO()
        err = StringIO()
        with self.assertRaises(Exception):
            call_command(
                "test_notify",
                "nonexistent",
                "--non-interactive",
                stdout=out,
                stderr=err,
            )

    def test_default_mode_is_interactive(self):
        """Running without --non-interactive enters interactive mode."""
        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        # Mock input() to select channel 1, accept defaults, then "done"
        with patch(
            "builtins.input",
            side_effect=["1", "", "", "", "3"],
        ), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ):
            call_command("test_notify", stdout=out)
        self.assertIn("Test Notification Wizard", out.getvalue())
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py -v`
Expected: FAIL — `test_non_interactive_with_db_channel_sends` fails because `--non-interactive` flag doesn't exist yet. `test_default_mode_is_interactive` fails because wizard doesn't exist yet.

**Step 3: Add --non-interactive flag and rename handle()**

In `apps/notify/management/commands/test_notify.py`:

1. Add `--non-interactive` argument in `add_arguments`:

```python
parser.add_argument(
    "--non-interactive",
    action="store_true",
    help="Skip interactive wizard; use CLI flags only (for scripts/CI).",
)
```

2. Rename `handle()` → `_handle_non_interactive()`. Create new `handle()` that routes:

```python
def handle(self, *args, **options):
    if options.get("non_interactive"):
        self._handle_non_interactive(*args, **options)
    else:
        self._handle_interactive(**options)

def _handle_interactive(self, **options):
    """Interactive wizard — placeholder for Task 2."""
    self.stdout.write(self.style.HTTP_INFO("\n=== Test Notification Wizard ===\n"))
    self.stderr.write(self.style.WARNING("Interactive mode not yet implemented."))

def _handle_non_interactive(self, *args, **options):
    # ... existing handle() body, unchanged ...
```

**Step 4: Run tests to verify the first two pass**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py::NonInteractiveTests -v`
Expected: `test_non_interactive_with_db_channel_sends` and `test_non_interactive_unknown_driver_raises` PASS. `test_default_mode_is_interactive` still fails (placeholder).

**Step 5: Commit**

```bash
git add apps/notify/management/commands/test_notify.py apps/notify/_tests/test_test_notify.py
git commit -m "refactor: rename handle() to _handle_non_interactive, add --non-interactive flag"
```

---

### Task 2: Implement _select_channel — channel discovery and selection

**Files:**
- Modify: `apps/notify/management/commands/test_notify.py`
- Test: `apps/notify/_tests/test_test_notify.py`

**Step 1: Write failing tests for channel selection**

Add to `apps/notify/_tests/test_test_notify.py`:

```python
class SelectChannelTests(TestCase):
    """Tests for interactive channel selection."""

    def _call_interactive(self, inputs, channels=None):
        """Helper: create channels, mock input, call command, return stdout."""
        for ch in (channels or []):
            NotificationChannel.objects.create(**ch)
        out = StringIO()
        with patch("builtins.input", side_effect=inputs), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.send",
            return_value={"success": True, "message_id": "m2", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
            return_value=True,
        ):
            call_command("test_notify", stdout=out)
        return out.getvalue()

    def test_lists_active_channels(self):
        """Wizard lists active DB channels with index numbers."""
        output = self._call_interactive(
            # Select channel 1, accept defaults, done
            ["1", "", "", "", "3"],
            channels=[
                {
                    "name": "ops-slack",
                    "driver": "slack",
                    "config": {"webhook_url": "https://hooks.slack.com/services/T/B/X"},
                },
            ],
        )
        self.assertIn("ops-slack", output)
        self.assertIn("slack", output)

    def test_no_channels_shows_configure_only(self):
        """When no active channels exist, only 'Configure new' is shown."""
        output = self._call_interactive(
            # Only option is "configure new" → pick generic → endpoint → accept defaults → done
            ["1", "generic", "https://example.com/hook", "", "", "", "3"],
        )
        self.assertIn("Configure a new driver", output)

    def test_invalid_selection_retries(self):
        """Invalid selection prompts again."""
        output = self._call_interactive(
            # Invalid "99", then valid "1", accept defaults, done
            ["99", "1", "", "", "", "3"],
            channels=[
                {
                    "name": "ops-slack",
                    "driver": "slack",
                    "config": {"webhook_url": "https://hooks.slack.com/services/T/B/X"},
                },
            ],
        )
        self.assertIn("ops-slack", output)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py::SelectChannelTests -v`
Expected: FAIL — `_handle_interactive` is a placeholder.

**Step 3: Implement _select_channel and wire into _handle_interactive**

Add to `test_notify.py` Command class:

```python
def _prompt_choice(self, prompt, options):
    """Prompt user to select one option from a numbered list.

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
        self.stdout.write(
            self.style.WARNING(f"  Please enter 1-{len(options)}.")
        )

def _prompt_input(self, prompt, default=None, required=False):
    """Prompt user for free-text input.

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

def _select_channel(self):
    """List active DB channels and let user pick one, or configure new.

    Returns:
        (driver_name, config, selected_label) tuple.
    """
    from apps.notify.models import NotificationChannel

    channels = list(
        NotificationChannel.objects.filter(is_active=True).order_by("name")
    )

    options = []
    for ch in channels:
        options.append(
            (ch, f"{ch.name} ({ch.driver}) — {ch.description or 'no description'}")
        )
    options.append(("__new__", "Configure a new driver manually"))

    choice = self._prompt_choice("Active notification channels:", options)

    if choice == "__new__":
        return self._configure_new_driver()

    # choice is a NotificationChannel instance
    return choice.driver, choice.config or {}, choice.name
```

Update `_handle_interactive`:

```python
def _handle_interactive(self, **options):
    """Interactive wizard for testing notifications."""
    self.stdout.write(
        self.style.HTTP_INFO("\n=== Test Notification Wizard ===\n")
    )

    driver_name, config, selected_label = self._select_channel()
    msg_opts = self._prompt_message_options()
    result = self._send_and_show_result(driver_name, config, selected_label, msg_opts)

    # Post-send loop
    while True:
        action = self._post_send_loop()
        if action == "done":
            break
        elif action == "retry":
            msg_opts = self._prompt_message_options(defaults=msg_opts)
            result = self._send_and_show_result(
                driver_name, config, selected_label, msg_opts
            )
        elif action == "switch":
            driver_name, config, selected_label = self._select_channel()
            msg_opts = self._prompt_message_options(defaults=msg_opts)
            result = self._send_and_show_result(
                driver_name, config, selected_label, msg_opts
            )
```

Add stubs for `_configure_new_driver`, `_prompt_message_options`, `_send_and_show_result`, `_post_send_loop` — implement in subsequent tasks.

For this task, stub them minimally so channel selection tests pass:

```python
def _configure_new_driver(self):
    """Prompt for driver type and config — implemented in Task 3."""
    driver = self._prompt_choice(
        "Select driver:", [(d, d) for d in DRIVER_REGISTRY]
    )
    config = self._build_config_interactive(driver)
    return driver, config, driver

def _build_config_interactive(self, driver_name):
    """Prompt for driver-specific config fields."""
    if driver_name == "slack":
        return {"webhook_url": self._prompt_input("Webhook URL", required=True)}
    elif driver_name == "email":
        return {
            "smtp_host": self._prompt_input("SMTP host", required=True),
            "from_address": self._prompt_input("From address", required=True),
            "smtp_port": int(self._prompt_input("SMTP port", default="587")),
        }
    elif driver_name == "pagerduty":
        return {
            "integration_key": self._prompt_input("Integration key", required=True)
        }
    elif driver_name == "generic":
        return {"endpoint": self._prompt_input("Endpoint URL", required=True)}
    return {}

def _prompt_message_options(self, defaults=None):
    """Collect title, message, severity from user."""
    defaults = defaults or {}
    title = self._prompt_input("Title", default=defaults.get("title", "Test Alert"))
    message = self._prompt_input(
        "Message",
        default=defaults.get(
            "message", "This is a test notification from the notify app."
        ),
    )
    severity = self._prompt_input(
        "Severity (critical/warning/info/success)",
        default=defaults.get("severity", "info"),
    )
    return {"title": title, "message": message, "severity": severity}

def _send_and_show_result(self, driver_name, config, selected_label, msg_opts):
    """Send notification and display result."""
    driver_class = DRIVER_REGISTRY.get(driver_name)
    if not driver_class:
        self.stderr.write(
            self.style.ERROR(f"Unknown driver: {driver_name}")
        )
        return {"success": False}

    driver = driver_class()
    message = NotificationMessage(
        title=msg_opts["title"],
        message=msg_opts["message"],
        severity=msg_opts["severity"],
    )

    if not driver.validate_config(config):
        self.stderr.write(
            self.style.ERROR(
                f"Invalid config for {driver_name}. Missing required fields."
            )
        )
        return {"success": False}

    self.stdout.write(
        self.style.WARNING(
            f"\nSending test notification to {selected_label} ({driver_name})..."
        )
    )

    result = driver.send(message, config)

    success = bool(result.get("success") or (result.get("status") == "success"))
    if success:
        self.stdout.write(self.style.SUCCESS("\n✓ Notification sent successfully!"))
        msg_id = result.get("message_id", "")
        if msg_id:
            self.stdout.write(f"  Message ID: {msg_id}")
        if result.get("metadata"):
            self.stdout.write(
                f"  Metadata: {json.dumps(result['metadata'], indent=2)}"
            )
    else:
        self.stdout.write(self.style.ERROR("\n✗ Failed to send notification"))
        self.stdout.write(
            f"  Error: {result.get('error') or result.get('message') or result}"
        )

    return result

def _post_send_loop(self):
    """Ask what to do after sending."""
    return self._prompt_choice(
        "What next?",
        [
            ("retry", "Retry with changes"),
            ("switch", "Send to a different channel"),
            ("done", "Done"),
        ],
    )
```

**Step 4: Run tests**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py -v`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add apps/notify/management/commands/test_notify.py apps/notify/_tests/test_test_notify.py
git commit -m "feat: add interactive channel selection wizard to test_notify"
```

---

### Task 3: Add tests for configure-new-driver interactive prompts

**Files:**
- Test: `apps/notify/_tests/test_test_notify.py`
- Modify: `apps/notify/management/commands/test_notify.py` (if needed)

**Step 1: Write failing tests**

Add to test file:

```python
class ConfigureNewDriverTests(TestCase):
    """Tests for interactive new-driver configuration."""

    def _call_interactive(self, inputs):
        """Helper: mock input, call command, return stdout."""
        out = StringIO()
        with patch("builtins.input", side_effect=inputs), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ), patch(
            "apps.notify.drivers.email.EmailNotifyDriver.send",
            return_value={"success": True, "message_id": "m2", "metadata": {}},
        ), patch(
            "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.send",
            return_value={"success": True, "message_id": "m3", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.send",
            return_value={"success": True, "message_id": "m4", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
            return_value=True,
        ), patch(
            "apps.notify.drivers.pagerduty.PagerDutyNotifyDriver.validate_config",
            return_value=True,
        ):
            call_command("test_notify", stdout=out)
        return out.getvalue()

    def test_configure_slack(self):
        """Configuring new slack driver prompts for webhook URL."""
        # No DB channels → "Configure new" is option 1 → pick slack (1) → webhook → defaults → done
        output = self._call_interactive(
            [
                "1",                                              # Configure new
                "1",                                              # slack (first in DRIVER_REGISTRY)
                "https://hooks.slack.com/services/T/B/X",         # webhook_url
                "", "", "",                                       # title, message, severity defaults
                "3",                                              # done
            ]
        )
        self.assertIn("successfully", output)

    def test_configure_generic(self):
        """Configuring new generic driver prompts for endpoint."""
        output = self._call_interactive(
            [
                "1",                              # Configure new
                "4",                              # generic (4th in registry)
                "https://example.com/hook",       # endpoint
                "", "", "",                       # defaults
                "3",                              # done
            ]
        )
        self.assertIn("successfully", output)

    def test_configure_pagerduty(self):
        """Configuring new pagerduty driver prompts for integration key."""
        output = self._call_interactive(
            [
                "1",                                  # Configure new
                "3",                                  # pagerduty
                "abcdefghijklmnopqrstuvwxyz",          # integration_key (20+ chars)
                "", "", "",                           # defaults
                "3",                                  # done
            ]
        )
        self.assertIn("successfully", output)
```

**Step 2: Run tests to verify**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py::ConfigureNewDriverTests -v`
Expected: PASS if Task 2 stubs are correct. If FAIL, adjust `_build_config_interactive` and driver registry ordering.

**Note:** The DRIVER_REGISTRY is a dict. In Python 3.7+ dicts are insertion-ordered, so the order is: email(1), slack(2), pagerduty(3), generic(4). Adjust the test input indices accordingly:

Check the actual order in `test_notify.py` lines 23-28:
```python
DRIVER_REGISTRY = {
    "email": EmailNotifyDriver,      # 1
    "slack": SlackNotifyDriver,      # 2
    "pagerduty": PagerDutyNotifyDriver,  # 3
    "generic": GenericNotifyDriver,  # 4
}
```

So slack=2, pagerduty=3, generic=4. Fix the test indices in `side_effect` lists.

**Step 3: Fix tests if needed, then commit**

```bash
git add apps/notify/_tests/test_test_notify.py
git commit -m "test: add configure-new-driver interactive tests"
```

---

### Task 4: Add tests for message prompts and send result display

**Files:**
- Test: `apps/notify/_tests/test_test_notify.py`
- Modify: `apps/notify/management/commands/test_notify.py` (if needed)

**Step 1: Write tests for message options and result display**

Add to test file:

```python
class MessagePromptTests(TestCase):
    """Tests for message option prompting."""

    def test_defaults_accepted_on_enter(self):
        """Pressing Enter accepts default title, message, severity."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch("builtins.input", side_effect=["1", "", "", "", "3"]), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ) as mock_send:
            call_command("test_notify", stdout=out)
        # Verify defaults were passed to send
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg.title, "Test Alert")
        self.assertEqual(msg.severity, "info")

    def test_custom_values_used(self):
        """Custom title, message, severity are passed to the driver."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch(
            "builtins.input",
            side_effect=["1", "Deploy Alert", "Deploying v2.0", "warning", "3"],
        ), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ) as mock_send:
            call_command("test_notify", stdout=out)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg.title, "Deploy Alert")
        self.assertEqual(msg.message, "Deploying v2.0")
        self.assertEqual(msg.severity, "warning")


class SendResultDisplayTests(TestCase):
    """Tests for send result display."""

    def test_success_shows_message_id(self):
        """Successful send shows message ID."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch("builtins.input", side_effect=["1", "", "", "", "3"]), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={
                "success": True,
                "message_id": "abc-123",
                "metadata": {"channel": "#alerts"},
            },
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("successfully", output)
        self.assertIn("abc-123", output)

    def test_failure_shows_error(self):
        """Failed send shows error message."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch("builtins.input", side_effect=["1", "", "", "", "3"]), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": False, "error": "Connection refused"},
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("Failed", output)
        self.assertIn("Connection refused", output)

    def test_invalid_config_shows_error(self):
        """Invalid driver config shows validation error."""
        NotificationChannel.objects.create(
            name="bad-ch",
            driver="slack",
            config={},  # missing webhook_url
        )
        out = StringIO()
        err = StringIO()
        with patch("builtins.input", side_effect=["1", "", "", "", "3"]):
            call_command("test_notify", stdout=out, stderr=err)
        self.assertIn("Invalid config", err.getvalue())
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py::MessagePromptTests apps/notify/_tests/test_test_notify.py::SendResultDisplayTests -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/notify/_tests/test_test_notify.py
git commit -m "test: add message prompt and send result display tests"
```

---

### Task 5: Add tests for post-send loop (retry, switch, done)

**Files:**
- Test: `apps/notify/_tests/test_test_notify.py`
- Modify: `apps/notify/management/commands/test_notify.py` (if needed)

**Step 1: Write tests for the adjust-and-retry loop**

Add to test file:

```python
class PostSendLoopTests(TestCase):
    """Tests for the retry/switch/done loop after sending."""

    def test_retry_resends_with_new_options(self):
        """Retry prompts for new options and sends again."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch(
            "builtins.input",
            side_effect=[
                "1",                    # select channel
                "", "", "",             # accept defaults (title, msg, severity)
                "1",                    # retry
                "Retry Alert", "", "",  # new title, same msg/severity
                "3",                    # done
            ],
        ), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ) as mock_send:
            call_command("test_notify", stdout=out)
        # Should have been called twice
        self.assertEqual(mock_send.call_count, 2)
        # Second call should have new title
        second_msg = mock_send.call_args_list[1][0][0]
        self.assertEqual(second_msg.title, "Retry Alert")

    def test_switch_channel_resends(self):
        """Switch channel prompts for new channel and sends."""
        NotificationChannel.objects.create(
            name="ch-a",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        NotificationChannel.objects.create(
            name="ch-b",
            driver="generic",
            config={"endpoint": "https://example.com/hook"},
        )
        out = StringIO()
        with patch(
            "builtins.input",
            side_effect=[
                "1",            # select ch-a
                "", "", "",     # defaults
                "2",            # switch
                "2",            # select ch-b
                "", "", "",     # defaults
                "3",            # done
            ],
        ), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.send",
            return_value={"success": True, "message_id": "m2", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
            return_value=True,
        ):
            call_command("test_notify", stdout=out)
        output = out.getvalue()
        self.assertIn("ch-a", output)
        self.assertIn("ch-b", output)

    def test_done_exits_immediately(self):
        """Selecting 'Done' exits the wizard."""
        NotificationChannel.objects.create(
            name="test-ch",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch(
            "builtins.input", side_effect=["1", "", "", "", "3"]
        ), patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ) as mock_send:
            call_command("test_notify", stdout=out)
        self.assertEqual(mock_send.call_count, 1)
```

**Step 2: Run tests**

Run: `uv run pytest apps/notify/_tests/test_test_notify.py::PostSendLoopTests -v`
Expected: PASS

**Step 3: Commit**

```bash
git add apps/notify/_tests/test_test_notify.py
git commit -m "test: add post-send loop tests (retry, switch, done)"
```

---

### Task 6: Verify 100% branch coverage and fix gaps

**Files:**
- Test: `apps/notify/_tests/test_test_notify.py`
- Modify: `apps/notify/management/commands/test_notify.py` (if needed)

**Step 1: Run coverage**

```bash
uv run coverage run -m pytest apps/notify/_tests/test_test_notify.py -q
uv run coverage report --include="apps/notify/management/commands/test_notify.py" --show-missing
```

**Step 2: Identify uncovered branches**

Look at the `Missing` column. Common gaps:
- `_build_config_interactive` branches for email (smtp_port int conversion)
- `_prompt_choice` invalid input retry path
- `_prompt_input` required=True empty retry path
- `_handle_non_interactive` with no driver arg (fallback to first DB channel)
- Result display branches (metadata present vs absent, message_id present vs absent)

**Step 3: Write targeted tests for each gap**

Add tests that exercise the missing branches. For example:

```python
class EdgeCaseTests(TestCase):
    """Tests for edge cases and branch coverage."""

    def test_prompt_input_required_retries_on_empty(self):
        """Required prompt retries when user enters empty string."""
        # Covered by configure_new_driver tests if a required field gets empty input first

    def test_non_interactive_no_driver_uses_first_channel(self):
        """--non-interactive without driver arg uses first active channel."""
        NotificationChannel.objects.create(
            name="alpha",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
        )
        out = StringIO()
        with patch(
            "apps.notify.drivers.slack.SlackNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ):
            call_command("test_notify", "--non-interactive", stdout=out)
        self.assertIn("successfully", out.getvalue())

    def test_non_interactive_json_config_override(self):
        """--non-interactive with --json-config overrides DB config."""
        NotificationChannel.objects.create(
            name="ops-generic",
            driver="generic",
            config={"endpoint": "https://old.example.com"},
        )
        out = StringIO()
        with patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.send",
            return_value={"success": True, "message_id": "m1", "metadata": {}},
        ), patch(
            "apps.notify.drivers.generic.GenericNotifyDriver.validate_config",
            return_value=True,
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
```

**Step 4: Iterate until 100%**

Run coverage, add tests, repeat until 100% branch coverage.

**Step 5: Commit**

```bash
git add apps/notify/_tests/test_test_notify.py apps/notify/management/commands/test_notify.py
git commit -m "test: achieve 100% branch coverage for test_notify"
```

---

### Task 7: Update cli.sh test_notify_menu for interactive mode

**Files:**
- Modify: `bin/cli.sh:699-729`

**Step 1: Simplify test_notify_menu**

Since `test_notify` is now interactive by default, the cli.sh menu no longer needs to prompt for driver/channel/message. Replace the current `test_notify_menu` function:

**Current** (lines 699-729): Manually prompts for driver_name, channel, message.

**New:**

```bash
test_notify_menu() {
    show_banner
    echo -e "${BOLD}═══ Test Notification ═══${NC}"
    echo ""
    echo "Launch the interactive notification testing wizard."
    echo ""

    local options=(
        "Interactive wizard - guided channel selection and testing"
        "Non-interactive (specify driver) - for scripting"
        "Back"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py test_notify"
                ;;
            2)
                read -p "Enter driver name or channel name: " driver_name
                if [ -z "$driver_name" ]; then
                    echo -e "${RED}Driver name required${NC}"
                    return
                fi
                confirm_and_run "uv run python manage.py test_notify $driver_name --non-interactive"
                ;;
            3)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Commit**

```bash
git add bin/cli.sh
git commit -m "feat: update cli.sh test_notify_menu for interactive wizard"
```

---

### Task 8: Update documentation files

**Files:**
- Modify: `apps/notify/README.md:47-183`
- Modify: `docs/Setup-Guide.md` (add section after line 217)
- Modify: `apps/notify/agents.md` (add section)

**Step 1: Update apps/notify/README.md**

Replace the `test_notify` section (line 47 onwards) to add interactive mode first, then non-interactive. Insert before the existing `#### test_notify` line:

After the `#### \`test_notify\`` heading and first description line, add:

```markdown
##### Interactive mode (default)

Run without arguments to launch the interactive wizard:

```bash
uv run python manage.py test_notify
```

The wizard guides you through:
1. **Channel selection** — pick an existing DB channel or configure a new driver
2. **Message options** — set title, message, and severity (with sensible defaults)
3. **Send and review** — see success/failure, message ID, and metadata
4. **Adjust and retry** — retry with different options or switch channels

Example session:

```
=== Test Notification Wizard ===

Active notification channels:
  1) ops-slack (slack) — [setup_wizard] slack channel
  2) Configure a new driver manually

Select channel [1]: 1

  Title [Test Alert]:
  Message [This is a test notification...]:
  Severity (critical/warning/info/success) [info]: warning

Sending test notification to ops-slack (slack)...

✓ Notification sent successfully!
  Message ID: abc-123

What next?
  1) Retry with changes
  2) Send to a different channel
  3) Done

Select [3]: 3
```

##### Non-interactive mode

For scripts and CI, use `--non-interactive` to skip the wizard:

```bash
uv run python manage.py test_notify slack --non-interactive
uv run python manage.py test_notify ops-slack --non-interactive
```

All existing flags work in non-interactive mode (--title, --message, --severity, --webhook-url, etc.).
```

Add `--non-interactive` to the flag reference table:

```markdown
| `--non-interactive` | flag | — | Skip wizard; use CLI flags only |
```

**Step 2: Update docs/Setup-Guide.md**

After the "Step 9: Set up recurring monitoring with cron" section (line 217), add:

```markdown
### Step 10: Test your notification channels

Verify your notification channels are working before relying on them:

```bash
uv run python manage.py test_notify
```

The interactive wizard lists your configured channels and lets you send test notifications.
Pick a channel, customize the message if desired, and verify delivery. You can retry with
different options or switch channels without re-running the command.

For scripting or CI:

```bash
uv run python manage.py test_notify ops-slack --non-interactive
```
```

**Step 3: Update apps/notify/agents.md**

Add after the "How to extend with a new driver" section:

```markdown
## Interactive test_notify wizard

The `test_notify` command runs in interactive mode by default:

- Lists active `NotificationChannel` records for selection
- Prompts for driver config when "configure new" is chosen
- Collects title, message, severity with defaults
- Shows send result with message_id and metadata
- Offers retry/switch/done loop

For automation, use `--non-interactive` flag — all existing CLI flags still work.

The interactive flow uses `_prompt_choice` and `_prompt_input` helpers (same pattern
as `setup_instance`). When testing, mock `builtins.input` and the driver's `send()` method.
```

**Step 4: Commit**

```bash
git add apps/notify/README.md docs/Setup-Guide.md apps/notify/agents.md
git commit -m "docs: update README, Setup-Guide, agents.md for interactive test_notify"
```

---

### Task 9: Final verification

**Step 1: Run full test suite**

```bash
uv run pytest -v
```

Expected: All tests pass (1115+ tests).

**Step 2: Run coverage on new/modified files**

```bash
uv run coverage run -m pytest apps/notify/_tests/test_test_notify.py -q
uv run coverage report --include="apps/notify/management/commands/test_notify.py" --show-missing
```

Expected: 100% branch coverage.

**Step 3: Run formatting and linting**

```bash
uv run black --check .
uv run ruff check .
```

Expected: All clean.

**Step 4: Fix any issues found, then commit**

```bash
git add -A
git commit -m "chore: final verification and cleanup"
```