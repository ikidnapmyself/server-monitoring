---
title: "System Checks Expansion — Implementation Plan"
parent: Plans
nav_order: 79739697
---
# System Checks Expansion — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 9 new Django system checks, a `preflight` management command, and a `bin/check_system.sh` CLI script for comprehensive system validation.

**Architecture:** Expand `apps/checkers/checks.py` with new `@register`-ed check functions under `security`, `environment`, and `pipeline` tags. Add `preflight` management command that runs `django.core.checks.run_checks()` per tag group with formatted output. Add `bin/check_system.sh` for pre-Django shell checks that delegates to `preflight`.

**Tech Stack:** Django system checks framework (`django.core.checks`), `os`/`pathlib` for filesystem checks, bash for CLI script.

---

### Task 1: Security checks — debug mode and secret key

**Files:**
- Modify: `apps/checkers/checks.py`
- Test: `apps/checkers/_tests/test_checks.py`

**Step 1: Write the failing tests**

Add to `apps/checkers/_tests/test_checks.py`:

```python
from apps.checkers.checks import (
    check_crontab_configuration,
    check_database_connection,
    check_debug_mode,
    check_pending_migrations,
    check_secret_key_strength,
)


class SecurityChecksTests(TestCase):
    """Tests for security system checks (debug mode, secret key)."""

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_debug_mode_warns_when_true(self, mock_testing):
        with self.settings(DEBUG=True):
            errors = check_debug_mode(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W010")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_debug_mode_ok_when_false(self, mock_testing):
        with self.settings(DEBUG=False):
            errors = check_debug_mode(app_configs=None)
            self.assertEqual(errors, [])

    def test_debug_mode_skipped_in_tests(self):
        errors = check_debug_mode(app_configs=None)
        self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_warns_when_short(self, mock_testing):
        with self.settings(SECRET_KEY="short"):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W011")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_warns_when_insecure(self, mock_testing):
        with self.settings(SECRET_KEY="django-insecure-" + "x" * 50):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W011")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_secret_key_ok_when_strong(self, mock_testing):
        with self.settings(SECRET_KEY="a" * 50):
            errors = check_secret_key_strength(app_configs=None)
            self.assertEqual(errors, [])

    def test_secret_key_skipped_in_tests(self):
        errors = check_secret_key_strength(app_configs=None)
        self.assertEqual(errors, [])
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::SecurityChecksTests -v`
Expected: FAIL — `ImportError: cannot import name 'check_debug_mode'`

**Step 3: Write the implementation**

Add to `apps/checkers/checks.py`:

```python
@register("security")
def check_debug_mode(app_configs, **kwargs):
    """Check that DEBUG is not enabled in production."""
    from django.conf import settings

    if _is_testing():
        return []

    errors = []
    if settings.DEBUG:
        errors.append(
            Warning(
                "DEBUG mode is enabled",
                hint="Set DEBUG=False in production. DEBUG=True exposes sensitive information.",
                id="checkers.W010",
            )
        )
    return errors


@register("security")
def check_secret_key_strength(app_configs, **kwargs):
    """Check that SECRET_KEY is sufficiently strong."""
    from django.conf import settings

    if _is_testing():
        return []

    errors = []
    secret_key = getattr(settings, "SECRET_KEY", "")
    if len(secret_key) < 50 or "insecure" in secret_key.lower():
        errors.append(
            Warning(
                f"SECRET_KEY appears weak ({len(secret_key)} chars)",
                hint=(
                    "Generate a strong secret key: "
                    "python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
                ),
                id="checkers.W011",
            )
        )
    return errors
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::SecurityChecksTests -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/checks.py apps/checkers/_tests/test_checks.py
git commit -m "feat: add security system checks (debug mode, secret key)"
```

---

### Task 2: Environment checks — .env, env vars, writable dir

**Files:**
- Modify: `apps/checkers/checks.py`
- Test: `apps/checkers/_tests/test_checks.py`

**Step 1: Write the failing tests**

```python
class EnvironmentChecksTests(TestCase):
    """Tests for environment system checks (.env, env vars, writable dir)."""

    def test_env_file_warns_when_missing(self):
        with (
            self.settings(BASE_DIR="/nonexistent/path"),
            patch("os.path.isfile", return_value=False),
        ):
            errors = check_env_file_exists(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W012")

    def test_env_file_ok_when_present(self):
        with patch("os.path.isfile", return_value=True):
            errors = check_env_file_exists(app_configs=None)
            self.assertEqual(errors, [])

    def test_required_env_vars_warns_on_missing(self):
        sample_content = "DJANGO_DEBUG=1\nOPENAI_API_KEY=\n# optional\n# OPTIONAL_VAR=foo\n"
        with (
            patch("builtins.open", mock_open(read_data=sample_content)),
            patch("os.path.isfile", return_value=True),
            patch.dict("os.environ", {"DJANGO_DEBUG": "1"}, clear=False),
        ):
            errors = check_required_env_vars(app_configs=None)
            # OPENAI_API_KEY is in .env.sample but not in os.environ
            missing = [e for e in errors if "OPENAI_API_KEY" in e.msg]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0].id, "checkers.W013")

    def test_required_env_vars_ok_when_all_set(self):
        sample_content = "DJANGO_DEBUG=1\n"
        with (
            patch("builtins.open", mock_open(read_data=sample_content)),
            patch("os.path.isfile", return_value=True),
            patch.dict("os.environ", {"DJANGO_DEBUG": "1"}, clear=False),
        ):
            errors = check_required_env_vars(app_configs=None)
            self.assertEqual(errors, [])

    def test_required_env_vars_skips_when_no_sample(self):
        with patch("os.path.isfile", return_value=False):
            errors = check_required_env_vars(app_configs=None)
            self.assertEqual(errors, [])

    def test_base_dir_writable_ok(self):
        with patch("os.access", return_value=True):
            errors = check_base_dir_writable(app_configs=None)
            self.assertEqual(errors, [])

    def test_base_dir_not_writable_warns(self):
        with patch("os.access", return_value=False):
            errors = check_base_dir_writable(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W017")
```

Update import at top of test file:
```python
from unittest.mock import MagicMock, mock_open, patch

from apps.checkers.checks import (
    check_base_dir_writable,
    check_crontab_configuration,
    check_database_connection,
    check_debug_mode,
    check_env_file_exists,
    check_pending_migrations,
    check_required_env_vars,
    check_secret_key_strength,
)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::EnvironmentChecksTests -v`
Expected: FAIL — `ImportError`

**Step 3: Write the implementation**

Add to `apps/checkers/checks.py`:

```python
import re


@register("environment")
def check_env_file_exists(app_configs, **kwargs):
    """Check that .env file exists."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.isfile(env_path):
        errors.append(
            Warning(
                ".env file not found",
                hint="Copy .env.sample to .env and configure: cp .env.sample .env",
                id="checkers.W012",
            )
        )
    return errors


@register("environment")
def check_required_env_vars(app_configs, **kwargs):
    """Check that required env vars from .env.sample are set."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    sample_path = os.path.join(base_dir, ".env.sample")
    if not os.path.isfile(sample_path):
        return errors

    try:
        with open(sample_path) as f:
            content = f.read()
    except OSError:
        return errors

    # Parse variable names: lines starting with UPPER_CASE=
    # Skip lines after a "# optional" comment
    optional_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("# optional"):
            optional_section = True
            continue
        if stripped.startswith("#") or not stripped:
            # Non-optional comment or blank line resets optional flag
            if stripped and not stripped.lower().startswith("# optional"):
                optional_section = False
            continue

        match = re.match(r"^([A-Z][A-Z0-9_]*)=", stripped)
        if match and not optional_section:
            var_name = match.group(1)
            if var_name not in os.environ:
                errors.append(
                    Warning(
                        f"Environment variable {var_name} not set (defined in .env.sample)",
                        hint=f"Set {var_name} in your .env file or shell environment.",
                        id="checkers.W013",
                    )
                )
    return errors


@register("environment")
def check_base_dir_writable(app_configs, **kwargs):
    """Check that the project directory is writable."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    if base_dir and not os.access(base_dir, os.W_OK):
        errors.append(
            Warning(
                "Project directory is not writable",
                hint="Cron logs and other output require write access to the project directory.",
                id="checkers.W017",
            )
        )
    return errors
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::EnvironmentChecksTests -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/checks.py apps/checkers/_tests/test_checks.py
git commit -m "feat: add environment system checks (.env, env vars, writable dir)"
```

---

### Task 3: Pipeline checks — definitions and notification channels

**Files:**
- Modify: `apps/checkers/checks.py`
- Test: `apps/checkers/_tests/test_checks.py`

**Step 1: Write the failing tests**

```python
from apps.checkers.checks import (
    # ... existing imports ...
    check_notification_channels,
    check_pipeline_status,
)


class PipelineChecksTests(TestCase):
    """Tests for pipeline system checks (definitions, channels)."""

    def test_pipeline_status_info_with_definitions(self):
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="local-smart", config={"nodes": []}, is_active=True
        )
        PipelineDefinition.objects.create(
            name="ai-analyzed", config={"nodes": []}, is_active=False
        )
        errors = check_pipeline_status(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "checkers.I001")
        self.assertIn("2 pipeline", errors[0].msg)
        self.assertIn("1 active", errors[0].msg)

    def test_pipeline_status_info_with_none(self):
        errors = check_pipeline_status(app_configs=None)
        self.assertEqual(len(errors), 1)
        self.assertIn("0 pipeline", errors[0].msg)

    def test_notification_channels_warns_when_none_active(self):
        errors = check_notification_channels(app_configs=None)
        warns = [e for e in errors if e.id == "checkers.W014"]
        self.assertEqual(len(warns), 1)
        self.assertIn("No active notification channels", warns[0].msg)

    def test_notification_channels_warns_on_empty_config(self):
        from apps.notify.models import NotificationChannel

        NotificationChannel.objects.create(
            name="bad-ch", driver="slack", config={}, is_active=True
        )
        errors = check_notification_channels(app_configs=None)
        warns = [e for e in errors if e.id == "checkers.W014"]
        self.assertTrue(any("empty config" in w.msg.lower() for w in warns))

    def test_notification_channels_ok_when_valid(self):
        from apps.notify.models import NotificationChannel

        NotificationChannel.objects.create(
            name="ops-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/x"},
            is_active=True,
        )
        errors = check_notification_channels(app_configs=None)
        warns = [e for e in errors if e.id == "checkers.W014"]
        self.assertEqual(warns, [])
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::PipelineChecksTests -v`
Expected: FAIL — `ImportError`

**Step 3: Write the implementation**

Add to `apps/checkers/checks.py`. Note: need to import `Info` from `django.core.checks`:

```python
from django.core.checks import Error, Info, Tags, Warning, register


@register("pipeline")
def check_pipeline_status(app_configs, **kwargs):
    """Report pipeline definition counts (active/inactive)."""
    from apps.orchestration.models import PipelineDefinition

    errors = []
    try:
        definitions = list(PipelineDefinition.objects.all().values("name", "is_active"))
        total = len(definitions)
        active = sum(1 for d in definitions if d["is_active"])
        inactive = total - active
        names = ", ".join(
            f"{d['name']} ({'active' if d['is_active'] else 'inactive'})"
            for d in definitions
        )
        errors.append(
            Info(
                f"{total} pipeline definition(s) ({active} active, {inactive} inactive)"
                + (f": {names}" if names else ""),
                id="checkers.I001",
            )
        )
    except Exception as e:
        errors.append(
            Warning(
                f"Cannot check pipeline definitions: {e}",
                id="checkers.I001",
            )
        )
    return errors


@register("pipeline")
def check_notification_channels(app_configs, **kwargs):
    """Check notification channel health."""
    from apps.notify.models import NotificationChannel

    errors = []
    try:
        active_channels = list(
            NotificationChannel.objects.filter(is_active=True).values("name", "driver", "config")
        )
        if not active_channels:
            errors.append(
                Warning(
                    "No active notification channels configured",
                    hint=(
                        "Create notification channels via Django Admin or "
                        "run 'python manage.py setup_instance'."
                    ),
                    id="checkers.W014",
                )
            )
        else:
            for ch in active_channels:
                if not ch["config"]:
                    errors.append(
                        Warning(
                            f"Notification channel '{ch['name']}' ({ch['driver']}) has empty config",
                            hint=f"Configure {ch['driver']} settings for channel '{ch['name']}' in Django Admin.",
                            id="checkers.W014",
                        )
                    )
    except Exception as e:
        errors.append(
            Warning(
                f"Cannot check notification channels: {e}",
                id="checkers.W014",
            )
        )
    return errors
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::PipelineChecksTests -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add apps/checkers/checks.py apps/checkers/_tests/test_checks.py
git commit -m "feat: add pipeline system checks (definitions, channels)"
```

---

### Task 4: Cron log checks — freshness and size

**Files:**
- Modify: `apps/checkers/checks.py`
- Test: `apps/checkers/_tests/test_checks.py`

**Step 1: Write the failing tests**

```python
import time

from apps.checkers.checks import (
    # ... existing imports ...
    check_cron_log_freshness,
    check_cron_log_size,
)


class CronLogChecksTests(TestCase):
    """Tests for cron log system checks (freshness, size)."""

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_cron_log_freshness_warns_when_stale(self, mock_testing):
        stale_time = time.time() - 7200  # 2 hours ago
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.path.getmtime", return_value=stale_time),
            patch(
                "subprocess.run",
                return_value=MagicMock(
                    returncode=0,
                    stdout="server-maintanence check_and_alert",
                ),
            ),
        ):
            errors = check_cron_log_freshness(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W015")

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_cron_log_freshness_ok_when_recent(self, mock_testing):
        recent_time = time.time() - 60  # 1 minute ago
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.path.getmtime", return_value=recent_time),
            patch(
                "subprocess.run",
                return_value=MagicMock(
                    returncode=0,
                    stdout="server-maintanence check_and_alert",
                ),
            ),
        ):
            errors = check_cron_log_freshness(app_configs=None)
            self.assertEqual(errors, [])

    @patch("apps.checkers.checks._is_testing", return_value=False)
    def test_cron_log_freshness_skips_when_no_log(self, mock_testing):
        with (
            patch("os.path.isfile", return_value=False),
            patch(
                "subprocess.run",
                return_value=MagicMock(
                    returncode=0,
                    stdout="server-maintanence check_and_alert",
                ),
            ),
        ):
            errors = check_cron_log_freshness(app_configs=None)
            self.assertEqual(errors, [])

    def test_cron_log_freshness_skips_in_tests(self):
        errors = check_cron_log_freshness(app_configs=None)
        self.assertEqual(errors, [])

    def test_cron_log_size_warns_when_large(self):
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.path.getsize", return_value=60 * 1024 * 1024),  # 60MB
        ):
            errors = check_cron_log_size(app_configs=None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "checkers.W016")

    def test_cron_log_size_ok_when_small(self):
        with (
            patch("os.path.isfile", return_value=True),
            patch("os.path.getsize", return_value=1024),  # 1KB
        ):
            errors = check_cron_log_size(app_configs=None)
            self.assertEqual(errors, [])

    def test_cron_log_size_skips_when_no_log(self):
        with patch("os.path.isfile", return_value=False):
            errors = check_cron_log_size(app_configs=None)
            self.assertEqual(errors, [])
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::CronLogChecksTests -v`
Expected: FAIL — `ImportError`

**Step 3: Write the implementation**

Add to `apps/checkers/checks.py`:

```python
import time


@register("crontab")
def check_cron_log_freshness(app_configs, **kwargs):
    """Check that cron.log has been updated recently (if cron is configured)."""
    from django.conf import settings

    if _is_testing():
        return []

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    log_path = os.path.join(base_dir, "cron.log")

    # Only check if cron.log exists
    if not os.path.isfile(log_path):
        return errors

    # Only check if cron is configured
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0 or "server-maintanence" not in result.stdout:
            return errors  # No cron configured, skip freshness check
    except Exception:
        return errors

    try:
        mtime = os.path.getmtime(log_path)
        age_seconds = time.time() - mtime
        if age_seconds > 3600:  # 1 hour
            age_minutes = int(age_seconds / 60)
            errors.append(
                Warning(
                    f"cron.log last updated {age_minutes} minutes ago",
                    hint=(
                        "The cron log hasn't been updated in over an hour. "
                        "Check that the cron job is running: crontab -l"
                    ),
                    id="checkers.W015",
                )
            )
    except OSError:
        pass

    return errors


@register("crontab")
def check_cron_log_size(app_configs, **kwargs):
    """Check that cron.log is not too large."""
    from django.conf import settings

    errors = []
    base_dir = str(getattr(settings, "BASE_DIR", ""))
    log_path = os.path.join(base_dir, "cron.log")

    if not os.path.isfile(log_path):
        return errors

    try:
        size_bytes = os.path.getsize(log_path)
        max_size = 50 * 1024 * 1024  # 50MB
        if size_bytes > max_size:
            size_mb = size_bytes / (1024 * 1024)
            errors.append(
                Warning(
                    f"cron.log is {size_mb:.0f}MB (threshold: 50MB)",
                    hint="Consider log rotation: logrotate, or truncate with: > cron.log",
                    id="checkers.W016",
                )
            )
    except OSError:
        pass

    return errors
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/test_checks.py::CronLogChecksTests -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/checks.py apps/checkers/_tests/test_checks.py
git commit -m "feat: add cron log system checks (freshness, size)"
```

---

### Task 5: Preflight management command

**Files:**
- Create: `apps/checkers/management/commands/preflight.py`
- Test: `apps/checkers/_tests/test_commands.py`

**Step 1: Write the failing tests**

Add to `apps/checkers/_tests/test_commands.py`:

```python
class PreflightCommandTests(TestCase):
    """Tests for the preflight management command."""

    def test_preflight_runs_all_groups(self):
        out = StringIO()
        call_command("preflight", stdout=out)
        output = out.getvalue()
        self.assertIn("Preflight Check", output)
        self.assertIn("Summary", output)

    def test_preflight_only_filter(self):
        out = StringIO()
        call_command("preflight", "--only", "security", stdout=out)
        output = out.getvalue()
        self.assertIn("Security", output)
        # Should not contain other groups
        self.assertNotIn("Pipeline", output)

    def test_preflight_json_output(self):
        out = StringIO()
        call_command("preflight", "--json", stdout=out)
        import json

        data = json.loads(out.getvalue())
        self.assertIn("groups", data)
        self.assertIn("summary", data)

    def test_preflight_json_with_filter(self):
        out = StringIO()
        call_command("preflight", "--json", "--only", "security", stdout=out)
        import json

        data = json.loads(out.getvalue())
        self.assertIn("security", data["groups"])
        self.assertNotIn("pipeline", data["groups"])
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::PreflightCommandTests -v`
Expected: FAIL — command not found

**Step 3: Write the implementation**

Create `apps/checkers/management/commands/preflight.py`:

```python
"""
Management command for comprehensive system preflight checks.

Usage:
    python manage.py preflight                    # All checks, human output
    python manage.py preflight --only security    # Filter by tag(s)
    python manage.py preflight --json             # JSON output for CI
"""

import json
from typing import Any

from django.core.checks import Error, Info, Warning, run_checks
from django.core.management.base import BaseCommand

# Tag groups in display order
TAG_GROUPS: list[tuple[str, str]] = [
    ("security", "Security"),
    ("environment", "Environment"),
    ("pipeline", "Pipeline"),
    ("crontab", "Crontab"),
    ("migrations", "Migrations"),
    ("database", "Database"),
]


class Command(BaseCommand):
    help = "Run comprehensive system preflight checks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            type=str,
            default=None,
            help="Comma-separated list of check groups to run (e.g., security,environment)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            dest="json_output",
            help="Output results as JSON",
        )

    def handle(self, *args, **options):
        only = options.get("only")
        json_output = options.get("json_output", False)

        # Determine which tag groups to run
        if only:
            requested = {t.strip() for t in only.split(",")}
            groups = [(tag, label) for tag, label in TAG_GROUPS if tag in requested]
        else:
            groups = list(TAG_GROUPS)

        results: dict[str, dict[str, Any]] = {}
        total_passed = 0
        total_warnings = 0
        total_errors = 0

        for tag, label in groups:
            checks = run_checks(tags=[tag])
            group_errors = sum(1 for c in checks if isinstance(c, Error))
            group_warnings = sum(1 for c in checks if isinstance(c, Warning))
            group_infos = sum(1 for c in checks if isinstance(c, Info))

            results[tag] = {
                "label": label,
                "checks": [
                    {
                        "level": _level(c),
                        "message": c.msg,
                        "hint": c.hint or "",
                        "id": c.id,
                    }
                    for c in checks
                ],
                "errors": group_errors,
                "warnings": group_warnings,
                "infos": group_infos,
            }

            total_errors += group_errors
            total_warnings += group_warnings
            total_passed += len(checks) - group_errors - group_warnings

        if json_output:
            self.stdout.write(
                json.dumps(
                    {
                        "groups": results,
                        "summary": {
                            "passed": total_passed,
                            "warnings": total_warnings,
                            "errors": total_errors,
                        },
                    },
                    indent=2,
                )
            )
        else:
            self._display_human(results, total_passed, total_warnings, total_errors)

    def _display_human(
        self,
        results: dict[str, dict[str, Any]],
        passed: int,
        warnings: int,
        errors: int,
    ) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("=== Preflight Check ===\n"))

        for tag, group in results.items():
            self.stdout.write(self.style.MIGRATE_LABEL(group["label"]))
            checks = group["checks"]
            if not checks:
                self.stdout.write("  (no checks registered)")
            for check in checks:
                level = check["level"]
                msg = check["message"]
                if level == "error":
                    self.stdout.write(self.style.ERROR(f"  ERR  {msg}"))
                elif level == "warning":
                    self.stdout.write(self.style.WARNING(f"  WARN {msg}"))
                elif level == "info":
                    self.stdout.write(f"  INFO {msg}")
                else:
                    self.stdout.write(self.style.SUCCESS(f"  OK   {msg}"))
                if check["hint"]:
                    self.stdout.write(f"         {check['hint']}")
            self.stdout.write("")

        summary = f"Summary: {passed} passed, {warnings} warning(s), {errors} error(s)"
        if errors > 0:
            self.stdout.write(self.style.ERROR(summary))
        elif warnings > 0:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))


def _level(check) -> str:
    if isinstance(check, Error):
        return "error"
    if isinstance(check, Warning):
        return "warning"
    if isinstance(check, Info):
        return "info"
    return "ok"
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/test_commands.py::PreflightCommandTests -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/preflight.py apps/checkers/_tests/test_commands.py
git commit -m "feat: add preflight management command"
```

---

### Task 6: CLI script (`bin/check_system.sh`)

**Files:**
- Create: `bin/check_system.sh`

**Step 1: Write the script**

```bash
#!/bin/bash
#
# System check script for server-maintanence
# Runs shell-level pre-checks then delegates to manage.py preflight
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SHELL_ONLY=false
DJANGO_ONLY=false

for arg in "$@"; do
    case $arg in
        --shell-only) SHELL_ONLY=true ;;
        --django-only) DJANGO_ONLY=true ;;
        --help|-h)
            echo "Usage: bin/check_system.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --shell-only   Run only shell-level checks"
            echo "  --django-only  Run only Django preflight checks"
            echo "  --help         Show this help"
            exit 0
            ;;
    esac
done

passed=0
warned=0
failed=0

check_pass() { echo -e "  ${GREEN}OK${NC}   $1"; ((passed++)); }
check_warn() { echo -e "  ${YELLOW}WARN${NC} $1"; ((warned++)); }
check_fail() { echo -e "  ${RED}ERR${NC}  $1"; ((failed++)); }

# ---- Shell-level checks ----

run_shell_checks() {
    echo -e "\n${BOLD}=== Shell Checks ===${NC}\n"

    # uv installed
    if command -v uv &>/dev/null; then
        check_pass "uv is installed ($(uv --version 2>/dev/null || echo 'unknown'))"
    else
        check_fail "uv is not installed"
    fi

    # Python version
    py_version=$(python3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+')
    if [ -n "$py_version" ]; then
        major=$(echo "$py_version" | cut -d. -f1)
        minor=$(echo "$py_version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            check_pass "Python $py_version (>= 3.10)"
        else
            check_fail "Python $py_version (need >= 3.10)"
        fi
    else
        check_fail "Python 3 not found"
    fi

    # .env exists
    if [ -f "$PROJECT_DIR/.env" ]; then
        check_pass ".env file found"
    else
        check_warn ".env file not found (copy .env.sample to .env)"
    fi

    # .venv exists
    if [ -d "$PROJECT_DIR/.venv" ]; then
        check_pass ".venv directory found (dependencies installed)"
    else
        check_warn ".venv not found (run: uv sync --extra dev)"
    fi

    # cron.log writable
    if touch "$PROJECT_DIR/.check_system_test" 2>/dev/null; then
        rm -f "$PROJECT_DIR/.check_system_test"
        check_pass "Project directory is writable"
    else
        check_warn "Project directory is not writable"
    fi

    # Disk space (>1GB free)
    if command -v df &>/dev/null; then
        free_kb=$(df -k "$PROJECT_DIR" | tail -1 | awk '{print $4}')
        free_gb=$((free_kb / 1024 / 1024))
        if [ "$free_gb" -ge 1 ]; then
            check_pass "Disk space: ${free_gb}GB free"
        else
            check_warn "Low disk space: ${free_gb}GB free (< 1GB)"
        fi
    fi

    echo ""
}

# ---- Django checks ----

run_django_checks() {
    echo -e "${BOLD}=== Django Preflight ===${NC}\n"
    cd "$PROJECT_DIR"
    uv run python manage.py preflight
}

# ---- Main ----

echo -e "\n${BOLD}============================================${NC}"
echo -e "${BOLD}   server-maintanence System Check${NC}"
echo -e "${BOLD}============================================${NC}"

if [ "$DJANGO_ONLY" = true ]; then
    run_django_checks
elif [ "$SHELL_ONLY" = true ]; then
    run_shell_checks
    echo -e "Shell checks: ${passed} passed, ${warned} warning(s), ${failed} error(s)"
else
    run_shell_checks
    run_django_checks
fi
```

**Step 2: Make it executable**

```bash
chmod +x bin/check_system.sh
```

**Step 3: Verify it runs**

Run: `bin/check_system.sh --shell-only`
Expected: Shell checks output with OK/WARN results

**Step 4: Commit**

```bash
git add bin/check_system.sh
git commit -m "feat: add bin/check_system.sh for system-wide checks"
```

---

### Task 7: Coverage and edge cases

**Files:**
- Modify: `apps/checkers/_tests/test_checks.py`

**Step 1: Run coverage to identify gaps**

```bash
uv run coverage run --omit="*/migrations/*,*/__pycache__/*,**/tests.py,**/_tests/**" -m pytest apps/checkers/_tests/test_checks.py apps/checkers/_tests/test_commands.py -q
uv run coverage report --include="apps/checkers/checks.py,apps/checkers/management/commands/preflight.py" --show-missing
```

**Step 2: Add tests for uncovered branches**

Likely gaps:
- `check_required_env_vars` — OSError reading file, commented-out lines in .env.sample
- `check_cron_log_freshness` — OSError on `getmtime`
- `check_pipeline_status` — exception path
- `check_notification_channels` — exception path
- `preflight` command — edge cases

Write tests for each uncovered branch. Target 100% branch coverage.

**Step 3: Run coverage again to verify 100%**

```bash
uv run coverage report --include="apps/checkers/checks.py,apps/checkers/management/commands/preflight.py" --show-missing
```

Expected: 100% branch coverage

**Step 4: Run full test suite**

```bash
uv run pytest
```

Expected: All tests pass

**Step 5: Commit**

```bash
git add apps/checkers/_tests/test_checks.py
git commit -m "test: achieve 100% branch coverage for system checks"
```

---

### Task 8: Documentation updates

**Files:**
- Modify: `apps/checkers/README.md`
- Modify: `docs/Setup-Guide.md`
- Modify: `apps/checkers/agents.md`
- Modify: `CLAUDE.md`

**Step 1: Update checkers README**

Add to `apps/checkers/README.md`:
- "System Checks" section documenting all checks with tags and IDs
- "Preflight command" section with usage examples
- Tag reference table

**Step 2: Update Setup-Guide**

Add "Verifying Your Setup" section to `docs/Setup-Guide.md` after installation:

```markdown
## Verifying Your Setup

After installation and configuration, run the system check:

\```bash
bin/check_system.sh
\```

This runs shell-level checks (Python version, dependencies, disk space) followed by
Django-level checks (database, migrations, security, pipeline status).

For Django-only checks:
\```bash
uv run python manage.py preflight
uv run python manage.py preflight --only security,environment
uv run python manage.py preflight --json
\```
```

**Step 3: Update agents.md**

Add preflight command contract to `apps/checkers/agents.md`.

**Step 4: Update CLAUDE.md**

Add `preflight` to the Essential Commands section.

**Step 5: Commit**

```bash
git add apps/checkers/README.md docs/Setup-Guide.md apps/checkers/agents.md CLAUDE.md
git commit -m "docs: update documentation for system checks and preflight command"
```

---

### Task 9: Final verification

**Step 1: Run full test suite**

```bash
uv run pytest
```

Expected: All tests pass

**Step 2: Run code quality checks**

```bash
uv run black --check .
uv run ruff check .
uv run mypy .
```

Expected: All clean

**Step 3: Verify preflight command works end-to-end**

```bash
uv run python manage.py preflight
uv run python manage.py preflight --json
uv run python manage.py check --tag security
uv run python manage.py check --tag pipeline
```

**Step 4: Verify bin script works**

```bash
bin/check_system.sh --shell-only
bin/check_system.sh --django-only
```