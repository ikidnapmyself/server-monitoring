---
title: "System Status Command Implementation Plan"
parent: Plans
---

# System Status Command Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a `system_status` management command that shows system profile at a glance and flags configuration inconsistencies across .env, settings.py, database state, and filesystem.

**Architecture:** A new management command (`system_status`) renders a dashboard (profile, pipeline state, definitions) then runs consistency checks from modular check functions in `apps/checkers/status/`. Each module exposes `run() -> list[CheckResult]`. Supports `--json`, `--checks-only`, `--verbose` flags.

**Tech Stack:** Django management commands, dataclasses, `pathlib`, `re` for env parsing. Tests use `django.test.TestCase`, `unittest.mock`, `call_command`.

**Design doc:** `docs/plans/2026-04-05-system-status-design.md`

---

### Task 1: CheckResult dataclass and status package init

**Files:**
- Create: `apps/checkers/status/__init__.py`
- Test: `apps/checkers/_tests/status/__init__.py`
- Test: `apps/checkers/_tests/status/test_check_result.py`

**Step 1: Create test directory and write failing test**

Create `apps/checkers/_tests/status/__init__.py` (empty).

Create `apps/checkers/_tests/status/test_check_result.py`:

```python
"""Tests for CheckResult dataclass."""

from django.test import TestCase

from apps.checkers.status import CheckResult


class CheckResultTests(TestCase):
    def test_defaults(self):
        r = CheckResult(level="warn", message="something")
        self.assertEqual(r.level, "warn")
        self.assertEqual(r.message, "something")
        self.assertEqual(r.hint, "")
        self.assertEqual(r.category, "")

    def test_all_fields(self):
        r = CheckResult(
            level="error",
            message="bad config",
            hint="fix it",
            category="cluster",
        )
        self.assertEqual(r.level, "error")
        self.assertEqual(r.hint, "fix it")
        self.assertEqual(r.category, "cluster")

    def test_valid_levels(self):
        for level in ("ok", "info", "warn", "error"):
            r = CheckResult(level=level, message="test")
            self.assertEqual(r.level, level)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_check_result.py -v`
Expected: FAIL — `ImportError: cannot import name 'CheckResult' from 'apps.checkers.status'`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/__init__.py`:

```python
"""System status checks — cross-source configuration consistency."""

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Result from a status check."""

    level: str  # "ok", "info", "warn", "error"
    message: str
    hint: str = ""
    category: str = ""  # "env", "cluster", "runtime", "database", "installation"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_check_result.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/__init__.py apps/checkers/_tests/status/
git commit -m "feat(status): add CheckResult dataclass and status package"
```

---

### Task 2: Env file consistency checks

**Files:**
- Create: `apps/checkers/status/env_checks.py`
- Test: `apps/checkers/_tests/status/test_env_checks.py`

**Context:** This module parses `.env`, `.env.sample`, and `config/settings.py` to find drift between them. It uses file I/O via `pathlib`, not Django settings.

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_env_checks.py`:

```python
"""Tests for env file consistency checks."""

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from apps.checkers.status import CheckResult
from apps.checkers.status.env_checks import (
    parse_env_keys,
    parse_sample_keys,
    parse_settings_env_refs,
    run,
)


class ParseEnvKeysTests(TestCase):
    """Tests for .env file key parsing."""

    def test_parses_simple_keys(self):
        content = "FOO=bar\nBAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO", "BAZ"})

    def test_ignores_comments(self):
        content = "# comment\nFOO=bar\n# BAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO"})

    def test_ignores_blank_lines(self):
        content = "\nFOO=bar\n\n\nBAZ=qux\n"
        self.assertEqual(parse_env_keys(content), {"FOO", "BAZ"})

    def test_handles_values_with_equals(self):
        content = "URL=https://example.com?a=1&b=2\n"
        self.assertEqual(parse_env_keys(content), {"URL"})

    def test_empty_value(self):
        content = "FOO=\n"
        self.assertEqual(parse_env_keys(content), {"FOO"})

    def test_empty_content(self):
        self.assertEqual(parse_env_keys(""), set())


class ParseSampleKeysTests(TestCase):
    """Tests for .env.sample key parsing (includes commented-out keys)."""

    def test_parses_active_and_commented_keys(self):
        content = "FOO=bar\n# BAZ=qux\n# pure comment no equals\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, {"BAZ"})

    def test_double_hash_ignored(self):
        content = "## heading\nFOO=bar\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, {"FOO"})
        self.assertEqual(commented, set())

    def test_commented_with_space(self):
        content = "# OPTIONAL_KEY=default_value\n"
        active, commented = parse_sample_keys(content)
        self.assertEqual(active, set())
        self.assertEqual(commented, {"OPTIONAL_KEY"})


class ParseSettingsEnvRefsTests(TestCase):
    """Tests for extracting os.environ.get references from settings.py."""

    def test_parses_environ_get(self):
        content = 'FOO = os.environ.get("MY_VAR", "default")\n'
        self.assertEqual(parse_settings_env_refs(content), {"MY_VAR"})

    def test_parses_single_quotes(self):
        content = "FOO = os.environ.get('MY_VAR')\n"
        self.assertEqual(parse_settings_env_refs(content), {"MY_VAR"})

    def test_multiple_refs(self):
        content = (
            'A = os.environ.get("VAR_A", "")\n'
            'B = os.environ.get("VAR_B", "0")\n'
        )
        self.assertEqual(parse_settings_env_refs(content), {"VAR_A", "VAR_B"})

    def test_no_refs(self):
        self.assertEqual(parse_settings_env_refs("x = 1\n"), set())


class RunEnvChecksTests(TestCase):
    """Integration tests for the env checks run() function."""

    @patch("apps.checkers.status.env_checks._read_file")
    def test_missing_env_file(self, mock_read):
        mock_read.return_value = None  # .env not found
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any(".env file not found" in r.message for r in errors))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_missing_sample_file(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            return None  # .env.sample not found

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any(".env.sample not found" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_sample_key_missing_from_env(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\nMISSING_KEY=default\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("MISSING_KEY" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_env_key_not_in_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\nEXTRA=val\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return ""
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("EXTRA" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_settings_ref_missing_from_sample(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=bar\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("UNDOCUMENTED_VAR", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("UNDOCUMENTED_VAR" in r.message for r in warns))

    @patch("apps.checkers.status.env_checks._read_file")
    def test_all_consistent_returns_ok(self, mock_read):
        def side_effect(path):
            if path.name == ".env":
                return "FOO=bar\n"
            if path.name == ".env.sample":
                return "FOO=default\n"
            if path.name == "settings.py":
                return 'x = os.environ.get("FOO", "")\n'
            return None

        mock_read.side_effect = side_effect
        results = run(base_dir=Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_env_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.checkers.status.env_checks'`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/env_checks.py`:

```python
"""Env file consistency checks.

Compares .env, .env.sample, and config/settings.py to detect drift:
- Keys in .env.sample missing from .env
- Keys in .env not in .env.sample
- Keys referenced in settings.py missing from .env.sample
- Keys in .env.sample never referenced in settings.py
"""

import re
from pathlib import Path

from apps.checkers.status import CheckResult

CATEGORY = "env"


def _read_file(path: Path) -> str | None:
    """Read file contents or return None if missing."""
    try:
        return path.read_text()
    except (FileNotFoundError, PermissionError):
        return None


def parse_env_keys(content: str) -> set[str]:
    """Extract variable names from .env file content."""
    keys = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.add(key)
    return keys


def parse_sample_keys(content: str) -> tuple[set[str], set[str]]:
    """Extract active and commented-out keys from .env.sample.

    Returns:
        (active_keys, commented_keys)
    """
    active = set()
    commented = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Active key
        if not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key:
                active.add(key)
        # Single-hash commented key (not ## headings)
        elif stripped.startswith("#") and not stripped.startswith("##"):
            rest = stripped.lstrip("# ")
            if "=" in rest:
                key = rest.split("=", 1)[0].strip()
                # Filter out prose comments (key must look like an env var)
                if re.match(r"^[A-Z][A-Z0-9_]*$", key):
                    commented.add(key)
    return active, commented


def parse_settings_env_refs(content: str) -> set[str]:
    """Extract env var names from os.environ.get() calls in settings.py."""
    return set(re.findall(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']', content))


def run(base_dir: Path) -> list[CheckResult]:
    """Run all env file consistency checks."""
    results: list[CheckResult] = []

    # Read files
    env_content = _read_file(base_dir / ".env")
    if env_content is None:
        results.append(
            CheckResult(
                level="error",
                message=".env file not found",
                hint="Copy .env.sample to .env and configure it.",
                category=CATEGORY,
            )
        )
        return results

    sample_content = _read_file(base_dir / ".env.sample")
    if sample_content is None:
        results.append(
            CheckResult(
                level="warn",
                message=".env.sample not found",
                hint=".env.sample serves as the reference for expected env vars.",
                category=CATEGORY,
            )
        )
        return results

    settings_content = _read_file(base_dir / "config" / "settings.py")

    # Parse
    env_keys = parse_env_keys(env_content)
    sample_active, sample_commented = parse_sample_keys(sample_content)
    all_sample_keys = sample_active | sample_commented
    settings_refs = parse_settings_env_refs(settings_content) if settings_content else set()

    # Check: sample keys missing from .env
    missing_from_env = sample_active - env_keys
    for key in sorted(missing_from_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is in .env.sample but missing from .env",
                hint="Add it to .env or remove from .env.sample if no longer needed.",
                category=CATEGORY,
            )
        )

    # Check: .env keys not in .env.sample
    unknown_in_env = env_keys - all_sample_keys
    for key in sorted(unknown_in_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is in .env but not documented in .env.sample",
                hint="Add it to .env.sample so others know about it.",
                category=CATEGORY,
            )
        )

    # Check: settings.py references not in .env.sample
    undocumented = settings_refs - all_sample_keys
    for key in sorted(undocumented):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} is referenced in settings.py but missing from .env.sample",
                hint="Document it in .env.sample.",
                category=CATEGORY,
            )
        )

    # Check: .env.sample keys never referenced in settings.py
    if settings_refs:
        unreferenced = sample_active - settings_refs
        for key in sorted(unreferenced):
            results.append(
                CheckResult(
                    level="warn",
                    message=f"{key} is in .env.sample but never referenced in settings.py",
                    hint="Remove from .env.sample if no longer used, or it may be used in shell scripts only.",
                    category=CATEGORY,
                )
            )

    # If no issues found, emit OK
    if not results:
        results.append(
            CheckResult(level="ok", message="All .env keys are consistent", category=CATEGORY)
        )

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_env_checks.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/env_checks.py apps/checkers/_tests/status/test_env_checks.py
git commit -m "feat(status): add env file consistency checks"
```

---

### Task 3: Cluster profile coherence checks

**Files:**
- Create: `apps/checkers/status/cluster_checks.py`
- Test: `apps/checkers/_tests/status/test_cluster_checks.py`

**Context:** These checks read Django settings (already loaded from .env) to detect cluster role conflicts.

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_cluster_checks.py`:

```python
"""Tests for cluster profile coherence checks."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.cluster_checks import run


class ClusterChecksTests(TestCase):
    """Test cluster profile consistency."""

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_agent_and_hub_conflict(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("conflict" in r.message.lower() for r in errors))

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="node-1",
    )
    def test_agent_without_secret(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in warns))

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_agent_without_instance_id(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("INSTANCE_ID" in r.message for r in warns))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_hub_without_secret(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in errors))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_standalone_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_valid_agent_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_valid_hub_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_cluster_checks.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/cluster_checks.py`:

```python
"""Cluster profile coherence checks.

Detects conflicts in cluster role configuration (agent vs hub vs standalone).
"""

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "cluster"


def _get_role() -> str:
    """Derive cluster role from settings."""
    has_hub_url = bool(getattr(settings, "HUB_URL", ""))
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)

    if has_hub_url and cluster_enabled:
        return "conflict"
    if has_hub_url:
        return "agent"
    if cluster_enabled:
        return "hub"
    return "standalone"


def run() -> list[CheckResult]:
    """Run cluster profile coherence checks."""
    results: list[CheckResult] = []

    role = _get_role()
    hub_url = getattr(settings, "HUB_URL", "")
    secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")
    instance_id = getattr(settings, "INSTANCE_ID", "")

    if role == "conflict":
        results.append(
            CheckResult(
                level="error",
                message="Cluster role conflict: both HUB_URL and CLUSTER_ENABLED=1 are set",
                hint="An instance cannot be both an agent and a hub. Unset one.",
                category=CATEGORY,
            )
        )
        return results

    if role == "agent":
        if not secret:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Set WEBHOOK_SECRET_CLUSTER for signed payloads to the hub.",
                    category=CATEGORY,
                )
            )
        if not instance_id:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: INSTANCE_ID is empty",
                    hint="Set INSTANCE_ID to identify this agent (defaults to hostname at runtime).",
                    category=CATEGORY,
                )
            )

    if role == "hub":
        if not secret:
            results.append(
                CheckResult(
                    level="error",
                    message="Hub mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Hub must have WEBHOOK_SECRET_CLUSTER to verify agent payloads.",
                    category=CATEGORY,
                )
            )

    # OK if no issues
    if not results:
        if role == "standalone":
            results.append(
                CheckResult(level="ok", message="Standalone mode (no cluster)", category=CATEGORY)
            )
        else:
            results.append(
                CheckResult(level="ok", message=f"Cluster role: {role}", category=CATEGORY)
            )

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_cluster_checks.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/cluster_checks.py apps/checkers/_tests/status/test_cluster_checks.py
git commit -m "feat(status): add cluster profile coherence checks"
```

---

### Task 4: Runtime state checks

**Files:**
- Create: `apps/checkers/status/runtime_checks.py`
- Test: `apps/checkers/_tests/status/test_runtime_checks.py`

**Context:** Checks for contradictions between environment settings (prod debug, eager celery, metrics misconfiguration).

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_runtime_checks.py`:

```python
"""Tests for runtime state consistency checks."""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.runtime_checks import run


class RuntimeChecksTests(TestCase):
    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_debug_on_in_production(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("DEBUG" in r.message for r in errors))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=[])
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_no_allowed_hosts_in_production(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("ALLOWED_HOSTS" in r.message for r in errors))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_celery_eager_in_production(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("eager" in r.message.lower() for r in warns))

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="custom-host",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_statsd_configured_but_backend_logging(self):
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("StatsD" in r.message for r in infos))

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="statsd",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_statsd_backend_with_default_host(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("STATSD_HOST" in r.message for r in warns))

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["example.com"],
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_clean_production(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        DEBUG=True,
        CELERY_TASK_ALWAYS_EAGER=True,
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_dev_mode_allows_debug_and_eager(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_runtime_checks.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/runtime_checks.py`:

```python
"""Environment vs runtime state consistency checks.

Detects contradictions like DEBUG=True in production or Celery eager mode
in production.
"""

import os

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "runtime"


def _is_production() -> bool:
    return os.environ.get("DJANGO_ENV", "dev") in ("prod", "production")


def run() -> list[CheckResult]:
    """Run runtime state consistency checks."""
    results: list[CheckResult] = []
    prod = _is_production()

    # Production-only checks
    if prod:
        if settings.DEBUG:
            results.append(
                CheckResult(
                    level="error",
                    message="DEBUG is enabled in production",
                    hint="Set DJANGO_DEBUG=0 in .env for production.",
                    category=CATEGORY,
                )
            )

        if not settings.ALLOWED_HOSTS:
            results.append(
                CheckResult(
                    level="error",
                    message="ALLOWED_HOSTS is empty in production",
                    hint="Set DJANGO_ALLOWED_HOSTS in .env.",
                    category=CATEGORY,
                )
            )

        if settings.CELERY_TASK_ALWAYS_EAGER:
            results.append(
                CheckResult(
                    level="warn",
                    message="Celery is in eager mode in production",
                    hint="Set CELERY_TASK_ALWAYS_EAGER=0 for real task execution.",
                    category=CATEGORY,
                )
            )

    # Metrics consistency (any environment)
    backend = getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging")
    statsd_host = getattr(settings, "STATSD_HOST", "localhost")

    if backend == "logging" and statsd_host != "localhost":
        results.append(
            CheckResult(
                level="info",
                message=f"StatsD host is configured ({statsd_host}) but metrics backend is 'logging'",
                hint="Set ORCHESTRATION_METRICS_BACKEND=statsd to use StatsD.",
                category=CATEGORY,
            )
        )

    if backend == "statsd" and statsd_host == "localhost":
        results.append(
            CheckResult(
                level="warn",
                message="Metrics backend is 'statsd' but STATSD_HOST is still 'localhost'",
                hint="Set STATSD_HOST to your StatsD server address.",
                category=CATEGORY,
            )
        )

    if not results:
        results.append(
            CheckResult(level="ok", message="Runtime configuration is consistent", category=CATEGORY)
        )

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_runtime_checks.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/runtime_checks.py apps/checkers/_tests/status/test_runtime_checks.py
git commit -m "feat(status): add runtime state consistency checks"
```

---

### Task 5: Database vs config state checks

**Files:**
- Create: `apps/checkers/status/database_checks.py`
- Test: `apps/checkers/_tests/status/test_database_checks.py`

**Context:** Queries `PipelineDefinition`, `NotificationChannel`, and `IntelligenceProvider` models to detect conflicts with env config.

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_database_checks.py`:

```python
"""Tests for database vs config state checks."""

from django.test import TestCase, override_settings

from apps.checkers.status.database_checks import run
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class DatabaseChecksTests(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_active_pipeline_with_eager_celery(self):
        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("eager" in r.message.lower() for r in warns))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_active_pipeline_without_eager_is_ok(self):
        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        results = run()
        errors = [r for r in results if r.level in ("error", "warn")]
        # Should not warn about eager
        self.assertFalse(any("eager" in r.message.lower() for r in errors))

    def test_no_active_channels(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("notification channel" in r.message.lower() for r in warns))

    def test_active_channel_present(self):
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertFalse(any("notification channel" in r.message.lower() for r in warns))

    def test_no_active_definitions(self):
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("pipeline definition" in r.message.lower() for r in infos))

    @override_settings(ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=False)
    def test_intelligence_active_fallback_disabled(self):
        IntelligenceProvider.objects.create(
            name="test-ai", provider="claude", is_active=True
        )
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("fallback" in r.message.lower() for r in infos))
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_database_checks.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/database_checks.py`:

```python
"""Database vs config state consistency checks.

Compares database records (pipeline definitions, notification channels,
intelligence providers) against environment configuration.
"""

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "database"


def run() -> list[CheckResult]:
    """Run database vs config consistency checks."""
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineDefinition

    results: list[CheckResult] = []

    active_definitions = PipelineDefinition.objects.filter(is_active=True).count()
    active_channels = NotificationChannel.objects.filter(is_active=True).count()
    active_providers = IntelligenceProvider.objects.filter(is_active=True)

    # Active pipelines + Celery eager
    if active_definitions > 0 and settings.CELERY_TASK_ALWAYS_EAGER:
        results.append(
            CheckResult(
                level="warn",
                message=f"{active_definitions} active pipeline definition(s) but Celery is in eager mode",
                hint="Eager mode runs tasks inline — set CELERY_TASK_ALWAYS_EAGER=0 for async execution.",
                category=CATEGORY,
            )
        )

    # No active notification channels
    if active_channels == 0:
        results.append(
            CheckResult(
                level="warn",
                message="No active notification channels configured",
                hint="Add a notification channel via Django Admin.",
                category=CATEGORY,
            )
        )

    # No active pipeline definitions
    if active_definitions == 0:
        results.append(
            CheckResult(
                level="info",
                message="No active pipeline definitions",
                hint="Create one via Django Admin or run: manage.py setup_instance",
                category=CATEGORY,
            )
        )

    # Intelligence provider active but fallback disabled
    fallback = getattr(settings, "ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED", True)
    if active_providers.exists() and not fallback:
        results.append(
            CheckResult(
                level="info",
                message="Intelligence provider is active but fallback is disabled",
                hint="If the AI provider fails, the pipeline will fail. Set ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=1 to continue on failure.",
                category=CATEGORY,
            )
        )

    if not results:
        results.append(
            CheckResult(level="ok", message="Database state is consistent with config", category=CATEGORY)
        )

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_database_checks.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/database_checks.py apps/checkers/_tests/status/test_database_checks.py
git commit -m "feat(status): add database vs config consistency checks"
```

---

### Task 6: Installation state checks

**Files:**
- Create: `apps/checkers/status/installation_checks.py`
- Test: `apps/checkers/_tests/status/test_installation_checks.py`

**Context:** Checks filesystem state (aliases, hooks, cron, directory permissions). Uses `pathlib` and `subprocess` for crontab inspection.

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_installation_checks.py`:

```python
"""Tests for installation state checks."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from apps.checkers.status.installation_checks import run


class InstallationChecksTests(TestCase):
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    def test_aliases_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "aliases" not in str(p)
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("aliases" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    def test_precommit_missing_in_dev(self, mock_exists):
        mock_exists.side_effect = lambda p: "pre-commit" not in str(p)
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("pre-commit" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @patch("apps.checkers.status.installation_checks._check_crontab")
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_cron_missing_in_prod(self, mock_writable, mock_cron, mock_exists):
        mock_exists.return_value = True
        mock_cron.return_value = False
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("cron" in r.message.lower() for r in warns))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_logs_dir_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = False
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("logs" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(
        LOGS_DIR=Path("/fake/logs"),
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": Path("/fake/db.sqlite3")}},
    )
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_db_file_not_writable(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.side_effect = lambda p: "db" not in str(p)
        results = run(base_dir=Path("/fake"))
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("database" in r.message.lower() for r in errors))

    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    @patch("apps.checkers.status.installation_checks._path_exists")
    @override_settings(LOGS_DIR=Path("/fake/logs"))
    @patch("apps.checkers.status.installation_checks._is_writable")
    def test_all_ok_in_dev(self, mock_writable, mock_exists):
        mock_exists.return_value = True
        mock_writable.return_value = True
        results = run(base_dir=Path("/fake"))
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_installation_checks.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/installation_checks.py`:

```python
"""Installation state consistency checks.

Checks filesystem state: aliases, pre-commit hooks, cron, directory permissions.
"""

import os
import subprocess
from pathlib import Path

from django.conf import settings

from apps.checkers.status import CheckResult

CATEGORY = "installation"


def _path_exists(path: Path) -> bool:
    return path.exists()


def _is_writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _check_crontab() -> bool:
    """Check if any crontab entries reference this project."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "server-maintanence" in result.stdout or "manage.py" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _is_production() -> bool:
    return os.environ.get("DJANGO_ENV", "dev") in ("prod", "production")


def run(base_dir: Path) -> list[CheckResult]:
    """Run installation state checks."""
    results: list[CheckResult] = []
    prod = _is_production()

    # Dev-only checks
    if not prod:
        if not _path_exists(base_dir / "bin" / "aliases.sh"):
            results.append(
                CheckResult(
                    level="warn",
                    message="Shell aliases not installed",
                    hint="Run: bin/install.sh aliases",
                    category=CATEGORY,
                )
            )

        if not _path_exists(base_dir / ".git" / "hooks" / "pre-commit"):
            results.append(
                CheckResult(
                    level="warn",
                    message="Pre-commit hooks not installed",
                    hint="Run: uv run pre-commit install",
                    category=CATEGORY,
                )
            )

    # Production-only checks
    if prod:
        if not _check_crontab():
            results.append(
                CheckResult(
                    level="warn",
                    message="No cron jobs configured for this project",
                    hint="Run: bin/install.sh cron",
                    category=CATEGORY,
                )
            )

    # Directory permission checks (all environments)
    logs_dir = getattr(settings, "LOGS_DIR", base_dir / "logs")
    if _path_exists(logs_dir) and not _is_writable(logs_dir):
        results.append(
            CheckResult(
                level="error",
                message=f"Logs directory is not writable: {logs_dir}",
                hint="Fix permissions: chmod u+w on the logs directory.",
                category=CATEGORY,
            )
        )

    db_settings = settings.DATABASES.get("default", {})
    if db_settings.get("ENGINE", "").endswith("sqlite3"):
        db_path = Path(db_settings.get("NAME", ""))
        if db_path and _path_exists(db_path) and not _is_writable(db_path):
            results.append(
                CheckResult(
                    level="error",
                    message=f"Database file is not writable: {db_path}",
                    hint="Fix permissions on the SQLite database file.",
                    category=CATEGORY,
                )
            )

    if not results:
        results.append(
            CheckResult(level="ok", message="Installation state is consistent", category=CATEGORY)
        )

    return results
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_installation_checks.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/installation_checks.py apps/checkers/_tests/status/test_installation_checks.py
git commit -m "feat(status): add installation state consistency checks"
```

---

### Task 7: Dashboard renderer

**Files:**
- Create: `apps/checkers/status/dashboard.py`
- Test: `apps/checkers/_tests/status/test_dashboard.py`

**Context:** Renders the system profile, pipeline state, and pipeline definitions sections. Returns both structured data (for JSON) and human-readable strings.

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_dashboard.py`:

```python
"""Tests for the system status dashboard renderer."""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.dashboard import get_profile, get_pipeline_state, render_definition_chain
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition, PipelineRun


class GetProfileTests(TestCase):
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        DEBUG=False,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/db.sqlite3"}},
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="node-1",
        LOGS_DIR="/var/log/sm",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "bare"})
    def test_agent_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "agent")
        self.assertEqual(profile["hub_url"], "https://hub.example.com")
        self.assertEqual(profile["environment"], "prod")
        self.assertFalse(profile["debug"])
        self.assertEqual(profile["deploy_method"], "bare")
        self.assertEqual(profile["instance_id"], "node-1")

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        DEBUG=False,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/db.sqlite3"}},
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="",
        LOGS_DIR="/var/log/sm",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "docker"})
    def test_hub_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "hub")

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        DEBUG=True,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/db.sqlite3"}},
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=True,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="",
        LOGS_DIR="/tmp/logs",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_standalone_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "standalone")
        self.assertTrue(profile["debug"])
        self.assertTrue(profile["celery_eager"])


class GetPipelineStateTests(TestCase):
    def test_empty_state(self):
        state = get_pipeline_state()
        self.assertEqual(state["channels"], [])
        self.assertEqual(state["intelligence"], [])
        self.assertIsNone(state["last_run"])

    def test_with_channels_and_providers(self):
        NotificationChannel.objects.create(name="slack", driver="slack", is_active=True)
        NotificationChannel.objects.create(name="email", driver="email", is_active=False)
        IntelligenceProvider.objects.create(name="ai", provider="claude", is_active=True)
        state = get_pipeline_state()
        self.assertEqual(len(state["channels"]), 2)
        self.assertEqual(len(state["intelligence"]), 1)

    def test_last_run(self):
        run = PipelineRun.objects.create(
            trace_id="t1", run_id="r1", status="notified"
        )
        state = get_pipeline_state()
        self.assertIsNotNone(state["last_run"])
        self.assertEqual(state["last_run"]["status"], "notified")


class RenderDefinitionChainTests(TestCase):
    def test_renders_node_chain(self):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={
                "nodes": [
                    {"id": "ingest", "type": "alerts", "config": {"driver": "webhook"}, "next": "check"},
                    {"id": "check", "type": "checkers", "config": {"checkers": ["cpu", "memory"]}, "next": "notify"},
                    {"id": "notify", "type": "notify", "config": {"driver": "slack"}},
                ]
            },
            is_active=True,
        )
        chain = render_definition_chain(defn)
        self.assertIn("alerts", chain)
        self.assertIn("cpu", chain)
        self.assertIn("notify", chain)
        self.assertIn("→", chain)

    def test_empty_config(self):
        defn = PipelineDefinition.objects.create(name="empty", config={}, is_active=True)
        chain = render_definition_chain(defn)
        self.assertEqual(chain, "(no stages)")
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `apps/checkers/status/dashboard.py`:

```python
"""System status dashboard data and rendering.

Produces structured data for the system profile, pipeline state,
and pipeline definitions. Used by the system_status command for
both human-readable and JSON output.
"""

import os
from pathlib import Path

from django.conf import settings


def get_profile() -> dict:
    """Build the system profile dictionary."""
    hub_url = getattr(settings, "HUB_URL", "")
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)

    if hub_url and cluster_enabled:
        role = "conflict"
    elif hub_url:
        role = "agent"
    elif cluster_enabled:
        role = "hub"
    else:
        role = "standalone"

    db_config = settings.DATABASES.get("default", {})
    db_name = str(db_config.get("NAME", ""))

    return {
        "role": role,
        "hub_url": hub_url,
        "environment": os.environ.get("DJANGO_ENV", "dev"),
        "debug": settings.DEBUG,
        "deploy_method": os.environ.get("DEPLOY_METHOD", "bare"),
        "database": db_name,
        "celery_broker": getattr(settings, "CELERY_BROKER_URL", ""),
        "celery_eager": getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False),
        "metrics_backend": getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging"),
        "instance_id": getattr(settings, "INSTANCE_ID", ""),
        "logs_dir": str(getattr(settings, "LOGS_DIR", "")),
    }


def get_pipeline_state() -> dict:
    """Build pipeline state: channels, intelligence, last run."""
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineRun

    channels = list(
        NotificationChannel.objects.all()
        .order_by("name")
        .values("name", "driver", "is_active")
    )
    intelligence = list(
        IntelligenceProvider.objects.filter(is_active=True)
        .order_by("name")
        .values("name", "provider", "is_active")
    )

    last_run_qs = PipelineRun.objects.order_by("-created_at").first()
    last_run = None
    if last_run_qs:
        last_run = {
            "timestamp": last_run_qs.created_at.isoformat() if last_run_qs.created_at else None,
            "status": last_run_qs.status,
            "run_id": last_run_qs.run_id,
        }

    return {
        "channels": channels,
        "intelligence": intelligence,
        "last_run": last_run,
    }


def get_definitions() -> list[dict]:
    """Build pipeline definitions with stage chains."""
    from apps.orchestration.models import PipelineDefinition

    definitions = []
    for defn in PipelineDefinition.objects.order_by("-is_active", "name"):
        definitions.append(
            {
                "name": defn.name,
                "active": defn.is_active,
                "chain": render_definition_chain(defn),
                "stages": _extract_stages(defn),
            }
        )
    return definitions


def render_definition_chain(defn) -> str:
    """Render a pipeline definition's nodes as a human-readable chain.

    Example: 'alerts: webhook → checkers: cpu,memory → notify: slack'
    """
    nodes = defn.get_nodes()
    if not nodes:
        return "(no stages)"

    parts = []
    for node in nodes:
        node_type = node.get("type", "unknown")
        config = node.get("config", {})

        # Extract the meaningful config values
        detail_parts = []
        for key in ("driver", "provider"):
            if key in config:
                detail_parts.append(config[key])
        if "checkers" in config:
            detail_parts.append(",".join(config["checkers"]))
        if "drivers" in config:
            detail_parts.append(",".join(config["drivers"]))
        if "channels" in config:
            detail_parts.append(",".join(config["channels"]))

        if detail_parts:
            parts.append(f"{node_type}: {','.join(detail_parts)}")
        else:
            parts.append(node_type)

    return " → ".join(parts)


def _extract_stages(defn) -> list[dict]:
    """Extract stages for JSON output."""
    nodes = defn.get_nodes()
    stages = []
    for node in nodes:
        stage = {"stage": node.get("type", "unknown")}
        config = node.get("config", {})
        for key in ("driver", "drivers", "provider", "providers", "checkers", "channels"):
            if key in config:
                val = config[key]
                stage[key] = val if isinstance(val, list) else [val]
        stages.append(stage)
    return stages
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_dashboard.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/status/dashboard.py apps/checkers/_tests/status/test_dashboard.py
git commit -m "feat(status): add dashboard renderer for system profile and pipeline state"
```

---

### Task 8: The system_status management command

**Files:**
- Create: `apps/checkers/management/commands/system_status.py`
- Test: `apps/checkers/_tests/status/test_command.py`

**Context:** Ties everything together. Renders dashboard, runs all check modules, formats output. Follow the patterns from `preflight.py` and `check_health.py` (using `self.style.*`, `self.stdout.write`, JSON support).

**Step 1: Write the failing tests**

Create `apps/checkers/_tests/status/test_command.py`:

```python
"""Tests for the system_status management command."""

import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class SystemStatusCommandTests(TestCase):
    """Tests for the system_status management command."""

    def _call(self, *args, **kwargs):
        out = StringIO()
        err = StringIO()
        call_command("system_status", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_human_output_contains_profile(self, mock_read):
        mock_read.return_value = None  # skip env checks
        output, _ = self._call()
        self.assertIn("System Profile", output)
        self.assertIn("Role:", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_output_is_valid(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("profile", data)
        self.assertIn("pipeline", data)
        self.assertIn("definitions", data)
        self.assertIn("checks", data)
        self.assertIn("summary", data)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_checks_only_skips_dashboard(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--checks-only")
        self.assertNotIn("System Profile", output)
        self.assertIn("Consistency", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_verbose_shows_ok_checks(self, mock_read):
        mock_read.return_value = None
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        output, _ = self._call("--verbose")
        self.assertIn("OK", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_pipeline_definitions_shown(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call()
        self.assertIn("Pipeline Definitions", output)
        self.assertIn("test-pipe", output)

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_definitions_include_stages(self, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertTrue(len(data["definitions"]) > 0)
        self.assertIn("stages", data["definitions"][0])

    @patch("apps.checkers.status.env_checks._read_file")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_counts(self, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        summary = data["summary"]
        self.assertIn("passed", summary)
        self.assertIn("warnings", summary)
        self.assertIn("errors", summary)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/status/test_command.py -v`
Expected: FAIL — `Unknown command: 'system_status'`

**Step 3: Write the command implementation**

Create `apps/checkers/management/commands/system_status.py`:

```python
"""
System status: configuration dashboard and consistency checks.

Usage:
    python manage.py system_status                # Dashboard + issues
    python manage.py system_status --json         # Full JSON for CI
    python manage.py system_status --checks-only  # Skip dashboard, issues only
    python manage.py system_status --verbose      # Include passing checks
"""

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.checkers.status import CheckResult
from apps.checkers.status.dashboard import get_definitions, get_pipeline_state, get_profile

# Check modules — each exposes run() -> list[CheckResult]
from apps.checkers.status import (
    cluster_checks,
    database_checks,
    env_checks,
    installation_checks,
    runtime_checks,
)

BASE_DIR = Path(settings.BASE_DIR)


class Command(BaseCommand):
    help = "Show system profile and flag configuration inconsistencies"
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            dest="json_output",
            help="Output as JSON",
        )
        parser.add_argument(
            "--checks-only",
            action="store_true",
            default=False,
            help="Skip dashboard, show only consistency checks",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Show passing checks too",
        )

    def handle(self, *args, **options):
        json_output = options["json_output"]
        checks_only = options["checks_only"]
        verbose = options["verbose"]

        # Collect data
        profile = get_profile()
        pipeline = get_pipeline_state()
        definitions = get_definitions()

        # Run all consistency checks
        all_checks: list[CheckResult] = []
        all_checks.extend(env_checks.run(base_dir=BASE_DIR))
        all_checks.extend(cluster_checks.run())
        all_checks.extend(runtime_checks.run())
        all_checks.extend(database_checks.run())
        all_checks.extend(installation_checks.run(base_dir=BASE_DIR))

        # Summary
        passed = sum(1 for c in all_checks if c.level == "ok")
        warnings = sum(1 for c in all_checks if c.level == "warn")
        errors = sum(1 for c in all_checks if c.level == "error")

        if json_output:
            self._output_json(profile, pipeline, definitions, all_checks, passed, warnings, errors)
        else:
            self._output_human(
                profile, pipeline, definitions, all_checks, passed, warnings, errors,
                checks_only=checks_only, verbose=verbose,
            )

    def _output_json(
        self,
        profile: dict,
        pipeline: dict,
        definitions: list[dict],
        checks: list[CheckResult],
        passed: int,
        warnings: int,
        errors: int,
    ) -> None:
        data = {
            "profile": profile,
            "pipeline": pipeline,
            "definitions": definitions,
            "checks": [
                {
                    "level": c.level,
                    "category": c.category,
                    "message": c.message,
                    "hint": c.hint,
                }
                for c in checks
                if c.level != "ok"
            ],
            "summary": {"passed": passed, "warnings": warnings, "errors": errors},
        }
        self.stdout.write(json.dumps(data, indent=2, default=str))

    def _output_human(
        self,
        profile: dict,
        pipeline: dict,
        definitions: list[dict],
        checks: list[CheckResult],
        passed: int,
        warnings: int,
        errors: int,
        checks_only: bool = False,
        verbose: bool = False,
    ) -> None:
        if not checks_only:
            self._render_profile(profile)
            self._render_pipeline_state(pipeline)
            self._render_definitions(definitions)

        self._render_checks(checks, verbose)
        self._render_summary(passed, warnings, errors)

    def _render_profile(self, profile: dict) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("\n═══ System Profile ══════════════════════════\n"))

        role_str = profile["role"]
        if role_str == "agent":
            role_str = f"agent → hub at {profile['hub_url']}"
        elif role_str == "hub":
            role_str = "hub (accepting cluster payloads)"
        elif role_str == "conflict":
            role_str = "CONFLICT (both agent and hub)"

        debug_str = "on" if profile["debug"] else "off"
        eager_str = "eager" if profile["celery_eager"] else "async"

        lines = [
            ("Role:", role_str),
            ("Environment:", f"{profile['environment']} (DEBUG={debug_str})"),
            ("Deploy:", profile["deploy_method"]),
            ("Database:", profile["database"]),
            ("Celery:", f"{profile['celery_broker']} ({eager_str})"),
            ("Metrics:", profile["metrics_backend"]),
            ("Logging:", profile["logs_dir"]),
        ]
        if profile["instance_id"]:
            lines.append(("Instance ID:", profile["instance_id"]))

        for label, value in lines:
            self.stdout.write(f"  {label:<14} {value}")
        self.stdout.write("")

    def _render_pipeline_state(self, pipeline: dict) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("═══ Pipeline State ══════════════════════════\n"))

        if pipeline["channels"]:
            ch_parts = []
            for ch in pipeline["channels"]:
                status = "active" if ch["is_active"] else "inactive"
                ch_parts.append(f"{ch['name']} ({status})")
            self.stdout.write(f"  {'Channels:':<14} {', '.join(ch_parts)}")
        else:
            self.stdout.write(f"  {'Channels:':<14} (none)")

        if pipeline["intelligence"]:
            int_parts = [f"{p['name']} ({p['provider']})" for p in pipeline["intelligence"]]
            self.stdout.write(f"  {'Intelligence:':<14} {', '.join(int_parts)}")
        else:
            self.stdout.write(f"  {'Intelligence:':<14} (none)")

        if pipeline["last_run"]:
            lr = pipeline["last_run"]
            self.stdout.write(f"  {'Last run:':<14} {lr['timestamp']} — {lr['status']}")
        else:
            self.stdout.write(f"  {'Last run:':<14} (none)")
        self.stdout.write("")

    def _render_definitions(self, definitions: list[dict]) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("═══ Pipeline Definitions ════════════════════\n"))

        if not definitions:
            self.stdout.write("  (none)")
            self.stdout.write("")
            return

        for defn in definitions:
            status = "active" if defn["active"] else "inactive"
            name_line = f"  {defn['name']} ({status})"
            if defn["active"]:
                self.stdout.write(self.style.SUCCESS(name_line))
            else:
                self.stdout.write(f"\033[2m{name_line}\033[0m")  # dim for inactive
            self.stdout.write(f"    {defn['chain']}")
        self.stdout.write("")

    def _render_checks(self, checks: list[CheckResult], verbose: bool) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("═══ Consistency ═════════════════════════════\n"))

        visible = checks if verbose else [c for c in checks if c.level != "ok"]

        if not visible:
            self.stdout.write(self.style.SUCCESS("  All checks passed"))
            self.stdout.write("")
            return

        for check in visible:
            if check.level == "error":
                self.stdout.write(self.style.ERROR(f"  ERR  {check.message}"))
            elif check.level == "warn":
                self.stdout.write(self.style.WARNING(f"  WARN {check.message}"))
            elif check.level == "info":
                self.stdout.write(f"  \033[34mINFO\033[0m {check.message}")
            else:
                self.stdout.write(self.style.SUCCESS(f"  OK   {check.message}"))
            if check.hint:
                self.stdout.write(f"         {check.hint}")
        self.stdout.write("")

    def _render_summary(self, passed: int, warnings: int, errors: int) -> None:
        summary = f"  {passed} passed, {warnings} warning(s), {errors} error(s)"
        if errors > 0:
            self.stdout.write(self.style.ERROR(summary))
        elif warnings > 0:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/status/test_command.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/system_status.py apps/checkers/_tests/status/test_command.py
git commit -m "feat(status): add system_status management command"
```

---

### Task 9: CLI integration — add `status` to bin/cli.sh

**Files:**
- Modify: `bin/cli/system.sh` — add "System status" option
- Test: Manual — run `bin/cli.sh system` and verify new option appears

**Step 1: Add the status option to system_menu**

In `bin/cli/system.sh`, add "System status (config consistency)" as the first option in the `options` array:

```bash
system_menu() {
    show_banner
    echo -e "${BOLD}═══ System & Security ═══${NC}"
    echo ""

    local options=(
        "System status (config consistency)"
        "System check (full preflight)"
        "Security audit"
        "Set production mode"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py system_status"
                ;;
            2)
                confirm_and_run "$SCRIPT_DIR/check_system.sh"
                ;;
            3)
                security_menu
                ;;
            4)
                confirm_and_run "$SCRIPT_DIR/set_production.sh"
                ;;
            5)
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

**Step 2: Verify manually**

Run: `bin/cli.sh system` (or just test the Django command directly)

Run: `uv run python manage.py system_status`
Expected: Dashboard output followed by consistency checks.

Run: `uv run python manage.py system_status --json`
Expected: Valid JSON output.

**Step 3: Commit**

```bash
git add bin/cli/system.sh
git commit -m "feat(cli): add system status option to system menu"
```

---

### Task 10: Full test suite run and coverage verification

**Step 1: Run all status tests**

Run: `uv run pytest apps/checkers/_tests/status/ -v`
Expected: All tests pass.

**Step 2: Run full test suite**

Run: `uv run pytest`
Expected: No regressions.

**Step 3: Check coverage**

Run: `uv run coverage run -m pytest && uv run coverage report --include="apps/checkers/status/*"`
Expected: 100% branch coverage on all status modules.

**Step 4: Run linters**

Run: `uv run black . && uv run ruff check . --fix`
Expected: No issues.

**Step 5: Final commit (if any formatting changes)**

```bash
git add -u
git commit -m "style: format system status module"
```