---
title: "Env Cleanup Implementation"
parent: Plans
---

# Environment Variable Cleanup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove application-level env vars that duplicate DB/definition-based config, clean up `.env.sample` to infrastructure-only, and remove legacy env var fallbacks from code.

**Architecture:** Pipeline definitions, `NotificationChannel`, and `IntelligenceProvider` models are the source of truth for application behavior. Environment variables only configure infrastructure (Django core, Celery, metrics). Code that reads removed env vars gets deleted or updated; tests that assert on removed env vars get rewritten.

**Tech Stack:** Django 5.2, Python, pytest

---

### Task 1: Remove CHECKERS_SKIP settings from settings.py

**Files:**
- Modify: `config/settings.py:198-214`

**Step 1: Remove the checker skip settings block**

Delete lines 198-214 in `config/settings.py` — the entire `Checkers Configuration` section:

```python
# DELETE THIS ENTIRE BLOCK:
# ---------------------------------------------------------------------------
# Checkers Configuration
# ---------------------------------------------------------------------------
# Disable all checkers globally.
# ...
CHECKERS_SKIP_ALL = os.environ.get("CHECKERS_SKIP_ALL", "0") in {"1", "true", "True", "yes", "on"}

# List of checker names to skip (disabled checkers).
# ...
_skip_checkers = os.environ.get("CHECKERS_SKIP", "")
CHECKERS_SKIP: list[str] = [c.strip() for c in _skip_checkers.split(",") if c.strip()]

# If CHECKERS_SKIP_ALL is enabled, treat all checkers as skipped.
# We do this by overriding the skip list downstream (in apps.checkers.checkers.is_checker_enabled).
```

**Step 2: Run tests to see what breaks**

Run: `uv run pytest apps/checkers/_tests/test_registry.py -v`
Expected: FAIL — tests use `@override_settings(CHECKERS_SKIP_ALL=...)` which now does nothing meaningful

**Step 3: Commit**

```bash
git add config/settings.py
git commit -m "refactor: remove CHECKERS_SKIP_ALL and CHECKERS_SKIP from settings.py"
```

---

### Task 2: Remove is_checker_enabled / get_enabled_checkers and their tests

**Files:**
- Modify: `apps/checkers/checkers/__init__.py:25-72`
- Modify: `apps/checkers/_tests/test_registry.py`

**Step 1: Remove is_checker_enabled and get_enabled_checkers from checkers/__init__.py**

In `apps/checkers/checkers/__init__.py`, delete:
- `"get_enabled_checkers"` and `"is_checker_enabled"` from `__all__` (lines 24-25)
- The `is_checker_enabled()` function (lines 41-61)
- The `get_enabled_checkers()` function (lines 64-72)

The file should only export the registry and checker classes:

```python
__all__ = [
    "BaseChecker",
    "CheckResult",
    "CheckStatus",
    "CPUChecker",
    "MemoryChecker",
    "DiskChecker",
    "DiskCommonChecker",
    "DiskLinuxChecker",
    "DiskMacOSChecker",
    "NetworkChecker",
    "ProcessChecker",
    "CHECKER_REGISTRY",
]
```

Add `"CHECKER_REGISTRY"` to `__all__` since external code uses it.

**Step 2: Remove checker skip tests from test_registry.py**

In `apps/checkers/_tests/test_registry.py`, delete:
- The import of `is_checker_enabled` (line 14)
- `test_skip_all_disables_every_checker` (lines 39-42)
- `test_skip_list_disables_only_selected_checkers` (lines 46-51)

**Step 3: Run tests**

Run: `uv run pytest apps/checkers/_tests/test_registry.py -v`
Expected: PASS (remaining registry tests should still work)

**Step 4: Commit**

```bash
git add apps/checkers/checkers/__init__.py apps/checkers/_tests/test_registry.py
git commit -m "refactor: remove is_checker_enabled and get_enabled_checkers

Pipeline definitions control which checkers run via context node checker_names."
```

---

### Task 3: Remove is_checker_enabled usage from alerts app

**Files:**
- Modify: `apps/alerts/check_integration.py:47,445-446,477-482`
- Modify: `apps/alerts/_tests/test_check_integration.py:151`
- Modify: `apps/alerts/management/commands/check_and_alert.py:33,88,109,121-126`

**Step 1: Update check_integration.py**

In `apps/alerts/check_integration.py`:
- Remove the import of `is_checker_enabled` (line 47)
- In `run_check_and_alert()`: remove the `if not is_checker_enabled(checker_name)` guard (lines 445-446). The caller (pipeline definition) already specifies which checkers to run.
- In `run_checks_and_alert()`: remove the `if not is_checker_enabled(checker_name)` skip (lines 477-482). Just iterate all provided checker names.

**Step 2: Update check_and_alert.py management command**

In `apps/alerts/management/commands/check_and_alert.py`:
- Remove the import of `is_checker_enabled` (line 33)
- Remove `--include-skipped` argument (line 88) — no skip list to override
- Remove the skip filtering logic (lines 109-126). Use all checkers from `CHECKER_REGISTRY` directly.

**Step 3: Update test_check_integration.py**

In `apps/alerts/_tests/test_check_integration.py`:
- Remove the mock patch of `is_checker_enabled` (line 151)

**Step 4: Run tests**

Run: `uv run pytest apps/alerts/_tests/ -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/check_integration.py apps/alerts/_tests/test_check_integration.py apps/alerts/management/commands/check_and_alert.py
git commit -m "refactor: remove is_checker_enabled from alerts app

Checker selection is now controlled by pipeline definitions, not global skip lists."
```

---

### Task 4: Remove NOTIFY_SKIP functions from notify drivers

**Files:**
- Modify: `apps/notify/drivers/__init__.py:15-16,28-59`

**Step 1: Remove is_notify_enabled and get_enabled_notify_drivers**

In `apps/notify/drivers/__init__.py`:
- Remove `"is_notify_enabled"` and `"get_enabled_notify_drivers"` from `__all__` (lines 15-16)
- Delete `is_notify_enabled()` function (lines 28-48)
- Delete `get_enabled_notify_drivers()` function (lines 51-59)

The file should be:

```python
"""
Notification drivers for sending notifications to various platforms.
"""

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver
from apps.notify.drivers.generic import GenericNotifyDriver
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver
from apps.notify.drivers.slack import SlackNotifyDriver

__all__ = [
    "NotificationMessage",
    "BaseNotifyDriver",
    "DRIVER_REGISTRY",
    "SlackNotifyDriver",
    "PagerDutyNotifyDriver",
    "EmailNotifyDriver",
    "GenericNotifyDriver",
]

# Registry of available notification drivers
DRIVER_REGISTRY: dict[str, type[BaseNotifyDriver]] = {
    "slack": SlackNotifyDriver,
    "pagerduty": PagerDutyNotifyDriver,
    "email": EmailNotifyDriver,
    "generic": GenericNotifyDriver,
}
```

**Step 2: Search for any callers of the removed functions**

Run: `uv run pytest apps/notify/_tests/ -v`
Expected: PASS (these functions were never called)

**Step 3: Commit**

```bash
git add apps/notify/drivers/__init__.py
git commit -m "refactor: remove unused is_notify_enabled and get_enabled_notify_drivers

NotificationChannel.is_active controls channel enable/disable in DB."
```

---

### Task 5: Remove OpenAI env var fallbacks from provider

**Files:**
- Modify: `apps/intelligence/providers/openai.py:8,36,39-40`
- Modify: `apps/intelligence/_tests/providers/test_openai.py:23-25`

**Step 1: Update the test to expect DB-only config**

In `apps/intelligence/_tests/providers/test_openai.py`, update `test_initialization_defaults()`:
- Remove the `patch.dict("os.environ", ...)` wrapper
- Test that when no args are passed, provider has empty/None values (no env fallback)
- Add a test that verifies provider works when config is passed via kwargs (the DB path)

```python
def test_initialization_defaults(self):
    """Provider uses empty defaults when no config provided."""
    provider = OpenAIRecommendationProvider()
    assert provider.api_key is None
    assert provider.model == "gpt-4o-mini"  # default_model class attr
    assert provider.max_tokens == 1024  # BaseAIProvider default

def test_initialization_with_kwargs(self):
    """Provider accepts config from DB (IntelligenceProvider model)."""
    provider = OpenAIRecommendationProvider(
        api_key="sk-test", model="gpt-4o", max_tokens=2048
    )
    assert provider.api_key == "sk-test"
    assert provider.model == "gpt-4o"
    assert provider.max_tokens == 2048
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/intelligence/_tests/providers/test_openai.py -v`
Expected: FAIL — provider still reads env vars

**Step 3: Update openai.py to remove env var fallbacks**

In `apps/intelligence/providers/openai.py`:
- Remove `import os` (line 8)
- Update `__init__` to remove all `os.environ.get()` calls:

```python
def __init__(
    self,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> None:
    super().__init__(
        api_key=api_key or "",
        model=model or "",
        max_tokens=max_tokens or 0,
    )
    if not api_key:
        self.api_key = None  # type: ignore[assignment]
    self._client = None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/intelligence/_tests/providers/test_openai.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/intelligence/providers/openai.py apps/intelligence/_tests/providers/test_openai.py
git commit -m "refactor: remove env var fallbacks from OpenAI provider

Credentials and config come from IntelligenceProvider DB model, not env vars."
```

---

### Task 6: Remove env var writing from setup_instance

**Files:**
- Modify: `apps/orchestration/management/commands/setup_instance.py:708-730`
- Modify: `apps/orchestration/_tests/test_setup_instance.py:1019-1051,1323-1327`

**Step 1: Update setup_instance handle() to stop writing removed env vars**

In `apps/orchestration/management/commands/setup_instance.py`, update the env_updates block (lines 708-730):

Remove these lines:
```python
# DELETE: CHECKERS_SKIP writing (lines 713-720)
if checkers:
    from apps.checkers.checkers import CHECKER_REGISTRY
    all_checkers = set(CHECKER_REGISTRY.keys())
    enabled = set(checkers["enabled"])
    skipped = all_checkers - enabled
    if skipped:
        env_updates["CHECKERS_SKIP"] = ",".join(sorted(skipped))

# DELETE: Intelligence env var writing (lines 722-727)
if intelligence:
    env_updates["INTELLIGENCE_PROVIDER"] = intelligence["provider"]
    if intelligence.get("api_key"):
        env_updates["OPENAI_API_KEY"] = intelligence["api_key"]
    if intelligence.get("model"):
        env_updates["OPENAI_MODEL"] = intelligence["model"]
```

Keep `env_updates = {}` and the `_write_env` call (it may still write `ALERTS_ENABLED_DRIVERS` on line 711). If `env_updates` ends up empty, consider skipping the `_write_env` call entirely:

```python
env_updates = {}

if alerts:
    env_updates["ALERTS_ENABLED_DRIVERS"] = ",".join(alerts)

if env_updates:
    self._write_env(env_path, env_updates)
    self.stdout.write(self.style.SUCCESS(f"✓ Updated .env with {len(env_updates)} setting(s)"))
```

**Step 2: Update setup_instance tests**

In `apps/orchestration/_tests/test_setup_instance.py`:

- `test_generates_checkers_skip_env` (line 1019): Rewrite to assert `CHECKERS_SKIP` is NOT in env_updates (setup no longer writes it)
- `test_generates_openai_env_vars` (line 1045): Rewrite to assert `INTELLIGENCE_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL` are NOT in env_updates
- `test_all_checkers_enabled_no_skip_env` (line 1323): This test already asserts CHECKERS_SKIP is absent — keep it, but simplify the name/docstring

**Step 3: Run tests**

Run: `uv run pytest apps/orchestration/_tests/test_setup_instance.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add apps/orchestration/management/commands/setup_instance.py apps/orchestration/_tests/test_setup_instance.py
git commit -m "refactor: stop writing CHECKERS_SKIP and OPENAI_* to .env from setup_instance

These are now DB-only: IntelligenceProvider model for AI config, pipeline definitions for checker selection."
```

---

### Task 7: Clean up .env.sample

**Files:**
- Modify: `.env.sample`

**Step 1: Rewrite .env.sample with infrastructure-only vars, organized**

```ini
# .env.sample
# Copy this file to .env for local development.
# Only infrastructure config belongs here. Application behavior
# (checkers, intelligence, notify) is configured via Django Admin
# and pipeline definitions.

# Django
DJANGO_SECRET_KEY=
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_ENV=dev

# Celery / Redis
CELERY_BROKER_URL=redis://localhost:6379/0
# CELERY_RESULT_BACKEND=redis://localhost:6379/0
CELERY_TASK_ALWAYS_EAGER=0

# Orchestration
ORCHESTRATION_MAX_RETRIES_PER_STAGE=3
ORCHESTRATION_BACKOFF_FACTOR=2.0
ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=1
ORCHESTRATION_METRICS_BACKEND=logging

# StatsD (only used when ORCHESTRATION_METRICS_BACKEND=statsd)
STATSD_HOST=localhost
STATSD_PORT=8125
STATSD_PREFIX=pipeline

# Django System Checks
# Silence specific checks during unrelated commands (migrate, runserver, etc.)
# The preflight command still shows all checks regardless.
# SILENCED_SYSTEM_CHECKS=checkers.W009,checkers.W010
```

**Step 2: Run the env var system check to make sure it still works**

Run: `uv run pytest apps/checkers/_tests/test_checks.py -v`
Expected: Some tests may need updating — `test_required_env_vars_warns_on_missing` uses `OPENAI_API_KEY` as a sample var. Update to use a var that still exists in .env.sample (e.g., `DJANGO_DEBUG`).

**Step 3: Update env var check tests**

In `apps/checkers/_tests/test_checks.py`, update:
- `test_required_env_vars_warns_on_missing` (line 198): Change sample content from `"DJANGO_DEBUG=1\nOPENAI_API_KEY=\n"` to `"DJANGO_DEBUG=1\nSTATSD_HOST=localhost\n"` and assert on `STATSD_HOST` instead of `OPENAI_API_KEY`
- `test_required_env_vars_ok_when_all_set` (line 213): Same — change `OPENAI_API_KEY` to `STATSD_HOST`

**Step 4: Run tests**

Run: `uv run pytest apps/checkers/_tests/test_checks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add .env.sample apps/checkers/_tests/test_checks.py
git commit -m "refactor: clean up .env.sample to infrastructure-only

Removed CHECKERS_SKIP*, NOTIFY_SKIP*, OPENAI_*, INTELLIGENCE_PROVIDER,
and DJANGO_DB_* keys. Application config lives in DB and pipeline definitions."
```

---

### Task 8: Update documentation

**Files:**
- Modify: `docs/Architecture.md:270-282`
- Modify: `apps/notify/README.md` (remove NOTIFY_SKIP docs)
- Modify: `apps/checkers/README.md` (remove CHECKERS_SKIP docs if present)

**Step 1: Update Architecture.md env var table**

Replace the env var table (lines 272-281) with:

```markdown
| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | Required in production |
| `DJANGO_DEBUG` | Debug mode | `0` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `*` |
| `CELERY_BROKER_URL` | Redis broker URL | `redis://localhost:6379/0` |
| `CELERY_TASK_ALWAYS_EAGER` | Run tasks synchronously (dev) | `False` |
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | Retries before pipeline failure | `3` |
| `ORCHESTRATION_BACKOFF_FACTOR` | Exponential backoff multiplier | `2.0` |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | Continue pipeline when AI fails | `1` |
| `ORCHESTRATION_METRICS_BACKEND` | Metrics backend (`logging` or `statsd`) | `logging` |
| `STATSD_HOST` | StatsD server host | `localhost` |
| `STATSD_PORT` | StatsD server port | `8125` |
| `STATSD_PREFIX` | StatsD metric prefix | `pipeline` |
```

Add a note after the table:

```markdown
Application-level configuration (which checkers to run, intelligence provider, notification channels) is managed through Django Admin and pipeline definitions — not environment variables. See the [Setup Guide](Setup-Guide) for details.
```

**Step 2: Update notify README.md**

Remove the `NOTIFY_SKIP_ALL` and `NOTIFY_SKIP` documentation section (around lines 140-172 per agent report).

**Step 3: Update checkers README.md**

Remove any `CHECKERS_SKIP` documentation if present.

**Step 4: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add docs/Architecture.md apps/notify/README.md apps/checkers/README.md
git commit -m "docs: update env var documentation to reflect cleanup

Removed references to CHECKERS_SKIP*, NOTIFY_SKIP*, OPENAI_* from docs.
Added orchestration and StatsD vars to Architecture.md table."
```