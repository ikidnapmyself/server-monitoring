---
title: "Unified Preflight Implementation Plan"
parent: Plans
---

# Unified Preflight Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate 5 overlapping check entry points into one `manage.py preflight` command with a dashboard header, flat check list, and JSON-line logging.

**Architecture:** Create `apps/checkers/preflight/` package with a flat `checks.py` containing all 28 check functions, a `dashboard.py` (moved from `status/`), and a `logger.py` for JSON-line logging. Rewrite the `preflight` management command. Shell scripts become thin wrappers. Delete `status/` and `system_status`.

**Tech Stack:** Django management commands, dataclasses, `pathlib`, `subprocess`, `shutil`, `json`, `logging`. Tests use `django.test.TestCase`, `unittest.mock`, `call_command`, `tempfile`.

**Design doc:** `docs/plans/2026-04-05-unified-preflight-design.md`

---

### Task 1: Create preflight package with CheckResult

**Files:**
- Create: `apps/checkers/preflight/__init__.py`
- Create: `apps/checkers/_tests/preflight/__init__.py`
- Test: `apps/checkers/_tests/preflight/test_check_result.py`

**Step 1: Write the failing test**

```python
"""Tests for CheckResult dataclass."""

from django.test import TestCase

from apps.checkers.preflight import CheckResult


class CheckResultTests(TestCase):
    def test_defaults(self):
        r = CheckResult(level="warn", message="something")
        self.assertEqual(r.level, "warn")
        self.assertEqual(r.message, "something")
        self.assertEqual(r.hint, "")

    def test_all_fields(self):
        r = CheckResult(level="error", message="bad", hint="fix it")
        self.assertEqual(r.hint, "fix it")

    def test_valid_levels(self):
        for level in ("ok", "info", "warn", "error"):
            r = CheckResult(level=level, message="test")
            self.assertEqual(r.level, level)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/preflight/test_check_result.py -v`
Expected: FAIL — ImportError

**Step 3: Write minimal implementation**

```python
"""Unified preflight checks — one command, one output, everything visible."""

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Result from a preflight check."""

    level: str  # "ok", "info", "warn", "error"
    message: str
    hint: str = ""
```

Note: `category` field is dropped — no grouping in the flat output.

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/preflight/test_check_result.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/checkers/preflight/ apps/checkers/_tests/preflight/
git commit -m "feat(preflight): add CheckResult dataclass and preflight package"
```

---

### Task 2: Move dashboard to preflight package

**Files:**
- Create: `apps/checkers/preflight/dashboard.py` (copy from `apps/checkers/status/dashboard.py`)
- Test: `apps/checkers/_tests/preflight/test_dashboard.py`

**Step 1: Copy dashboard.py**

Copy `apps/checkers/status/dashboard.py` to `apps/checkers/preflight/dashboard.py`. No changes needed — it has no imports from `apps.checkers.status`.

**Step 2: Write tests**

Copy `apps/checkers/_tests/status/test_dashboard.py` to `apps/checkers/_tests/preflight/test_dashboard.py`. Update all imports from `apps.checkers.status.dashboard` to `apps.checkers.preflight.dashboard`.

**Step 3: Run tests**

Run: `uv run pytest apps/checkers/_tests/preflight/test_dashboard.py -v`
Expected: PASS (all 12 tests)

**Step 4: Commit**

```bash
git add apps/checkers/preflight/dashboard.py apps/checkers/_tests/preflight/test_dashboard.py
git commit -m "feat(preflight): move dashboard renderer to preflight package"
```

---

### Task 3: Port existing checks into preflight/checks.py

**Files:**
- Create: `apps/checkers/preflight/checks.py`
- Test: `apps/checkers/_tests/preflight/test_checks.py`

**Context:** Flatten all check functions from `status/env_checks.py`, `status/cluster_checks.py`, `status/runtime_checks.py`, `status/database_checks.py`, and `status/installation_checks.py` into one file. Each function returns `list[CheckResult]`. Keep the same logic, just consolidate.

**Step 1: Write the checks module**

Create `apps/checkers/preflight/checks.py` with these sections, porting function bodies from the status modules:

```python
"""All preflight check functions.

Each function returns list[CheckResult]. Functions are called in order by
the preflight command to produce a flat list of results.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from apps.checkers.preflight import CheckResult

# ---------------------------------------------------------------------------
# Helpers (ported from status modules, kept private)
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> str | None:
    try:
        return path.read_text()
    except (FileNotFoundError, PermissionError):
        return None

def _path_exists(path: Path) -> bool:
    return path.exists()

def _is_writable(path: Path) -> bool:
    return os.access(path, os.W_OK)

def _check_crontab() -> bool:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
        return "server-maintanence" in result.stdout or "manage.py" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False

def _is_production() -> bool:
    return os.environ.get("DJANGO_ENV", "dev") in ("prod", "production")

def _parse_env_keys(content: str) -> set[str]:
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

def _parse_sample_keys(content: str) -> tuple[set[str], set[str]]:
    """Extract active and commented-out keys from .env.sample."""
    active = set()
    commented = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key:
                active.add(key)
        elif stripped.startswith("#") and not stripped.startswith("##"):
            rest = stripped.lstrip("# ")
            if "=" in rest:
                key = rest.split("=", 1)[0].strip()
                if re.match(r"^[A-Z][A-Z0-9_]*$", key):
                    commented.add(key)
    return active, commented

def _parse_settings_env_refs(content: str) -> set[str]:
    """Extract env var names from os.environ.get() calls."""
    return set(re.findall(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']', content))

# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

def check_python_version() -> list[CheckResult]:
    """Check Python >= 3.10 and running from project .venv."""
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}"
    if v < (3, 10):
        return [CheckResult(level="error", message=f"Python {version_str} (need >= 3.10)")]
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        return [CheckResult(
            level="warn",
            message=f"Python {version_str} — running outside virtualenv",
            hint="Run via: uv run python manage.py preflight",
        )]
    venv_is_project = ".venv" in sys.prefix
    if not venv_is_project:
        return [CheckResult(
            level="warn",
            message=f"Python {version_str} — not using project .venv",
            hint="Activate project venv or run via: uv run",
        )]
    return [CheckResult(level="ok", message=f"Python {version_str} (.venv)")]

def check_uv_installed() -> list[CheckResult]:
    """Check uv package manager is available."""
    import shutil
    if shutil.which("uv"):
        return [CheckResult(level="ok", message="uv is installed")]
    return [CheckResult(
        level="warn", message="uv is not installed",
        hint="Install: curl -LsSf https://astral.sh/uv/install.sh | sh",
    )]

def check_venv_exists(base_dir: Path) -> list[CheckResult]:
    """Check .venv directory exists."""
    if _path_exists(base_dir / ".venv"):
        return [CheckResult(level="ok", message=".venv directory found")]
    return [CheckResult(
        level="warn", message=".venv not found", hint="Run: uv sync",
    )]

def check_env_file_exists(base_dir: Path) -> list[CheckResult]:
    """Check .env file exists."""
    if _path_exists(base_dir / ".env"):
        return [CheckResult(level="ok", message=".env file found")]
    return [CheckResult(
        level="error", message=".env file not found",
        hint="Copy .env.sample to .env and configure it.",
    )]

def check_project_writable(base_dir: Path) -> list[CheckResult]:
    """Check project directory is writable."""
    if _is_writable(base_dir):
        return [CheckResult(level="ok", message="Project directory is writable")]
    return [CheckResult(level="warn", message="Project directory is not writable")]

def check_disk_space(base_dir: Path) -> list[CheckResult]:
    """Check at least 1GB free disk space."""
    import shutil
    try:
        usage = shutil.disk_usage(base_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb >= 1:
            return [CheckResult(level="ok", message=f"Disk space: {free_gb:.1f}GB free")]
        return [CheckResult(
            level="warn", message=f"Low disk space: {free_gb:.1f}GB free (< 1GB)",
        )]
    except OSError:
        return [CheckResult(level="warn", message="Could not check disk space")]

# ---------------------------------------------------------------------------
# Database checks
# ---------------------------------------------------------------------------

def check_database_connection() -> list[CheckResult]:
    """Check database connectivity."""
    from django.db import connections
    results = []
    for alias in connections:
        try:
            conn = connections[alias]
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            results.append(CheckResult(level="ok", message=f"Database '{alias}' connection works"))
        except Exception as e:
            results.append(CheckResult(
                level="error", message=f"Cannot connect to database '{alias}'",
                hint=str(e),
            ))
    return results

def check_pending_migrations() -> list[CheckResult]:
    """Check no pending migrations."""
    from django.db.migrations.executor import MigrationExecutor
    from django.db import connections
    try:
        conn = connections["default"]
        conn.ensure_connection()
        executor = MigrationExecutor(conn)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            return [CheckResult(
                level="warn",
                message=f"{len(plan)} pending migration(s)",
                hint="Run: uv run python manage.py migrate",
            )]
        return [CheckResult(level="ok", message="No pending migrations")]
    except Exception as e:
        return [CheckResult(level="error", message=f"Migration check failed: {e}")]

# ---------------------------------------------------------------------------
# Security checks
# ---------------------------------------------------------------------------

def check_debug_mode() -> list[CheckResult]:
    """Check DEBUG is appropriate for environment."""
    prod = _is_production()
    if prod and settings.DEBUG:
        return [CheckResult(
            level="error", message="DEBUG is enabled in production",
            hint="Set DJANGO_DEBUG=0 in .env.",
        )]
    debug_str = "on" if settings.DEBUG else "off"
    return [CheckResult(level="ok", message=f"DEBUG is {debug_str}")]

def check_secret_key_strength() -> list[CheckResult]:
    """Check SECRET_KEY is at least 50 characters."""
    key = getattr(settings, "SECRET_KEY", "")
    length = len(key)
    if length >= 50:
        return [CheckResult(level="ok", message=f"SECRET_KEY is {length} chars")]
    return [CheckResult(
        level="warn", message=f"SECRET_KEY is only {length} chars (need >= 50)",
        hint="Generate a stronger key.",
    )]

def check_allowed_hosts() -> list[CheckResult]:
    """Check ALLOWED_HOSTS is set in production."""
    prod = _is_production()
    if prod and not settings.ALLOWED_HOSTS:
        return [CheckResult(
            level="error", message="ALLOWED_HOSTS is empty in production",
            hint="Set DJANGO_ALLOWED_HOSTS in .env.",
        )]
    if "*" in settings.ALLOWED_HOSTS:
        return [CheckResult(
            level="warn", message="ALLOWED_HOSTS contains wildcard '*'",
            hint="Use specific hostnames in production.",
        )]
    return [CheckResult(level="ok", message="ALLOWED_HOSTS configured")]

def check_env_file_permissions(base_dir: Path) -> list[CheckResult]:
    """Check .env file is not world-readable."""
    env_path = base_dir / ".env"
    if not _path_exists(env_path):
        return []  # Already covered by check_env_file_exists
    try:
        mode = env_path.stat().st_mode
        if mode & 0o004:  # world-readable
            return [CheckResult(
                level="warn", message=".env file is world-readable",
                hint="Run: chmod 600 .env",
            )]
        return [CheckResult(level="ok", message=".env file permissions OK")]
    except OSError:
        return []

# ---------------------------------------------------------------------------
# Config consistency checks (ported from status/env_checks.py)
# ---------------------------------------------------------------------------

def check_env_consistency(base_dir: Path) -> list[CheckResult]:
    """Check .env vs .env.sample vs settings.py drift."""
    results: list[CheckResult] = []

    env_content = _read_file(base_dir / ".env")
    if env_content is None:
        return []  # Already covered by check_env_file_exists

    sample_content = _read_file(base_dir / ".env.sample")
    if sample_content is None:
        return [CheckResult(
            level="warn", message=".env.sample not found",
            hint=".env.sample serves as the reference for expected env vars.",
        )]

    settings_content = _read_file(base_dir / "config" / "settings.py")

    env_keys = _parse_env_keys(env_content)
    sample_active, sample_commented = _parse_sample_keys(sample_content)
    all_sample_keys = sample_active | sample_commented
    settings_refs = _parse_settings_env_refs(settings_content) if settings_content else set()

    missing_from_env = sample_active - env_keys
    for key in sorted(missing_from_env):
        results.append(CheckResult(
            level="warn", message=f"{key} in .env.sample but missing from .env",
            hint="Add it to .env or remove from .env.sample.",
        ))

    unknown_in_env = env_keys - all_sample_keys
    for key in sorted(unknown_in_env):
        results.append(CheckResult(
            level="warn", message=f"{key} in .env but not in .env.sample",
            hint="Add it to .env.sample so others know about it.",
        ))

    undocumented = settings_refs - all_sample_keys
    for key in sorted(undocumented):
        results.append(CheckResult(
            level="warn", message=f"{key} in settings.py but not in .env.sample",
            hint="Document it in .env.sample.",
        ))

    if settings_refs:
        unreferenced = sample_active - settings_refs
        for key in sorted(unreferenced):
            results.append(CheckResult(
                level="warn",
                message=f"{key} in .env.sample but unused in settings.py",
                hint="Remove if unused, or it may be shell-only.",
            ))

    if not results:
        results.append(CheckResult(level="ok", message="Env files are consistent"))

    return results

# ---------------------------------------------------------------------------
# Cluster coherence checks (ported from status/cluster_checks.py)
# ---------------------------------------------------------------------------

def check_cluster_coherence() -> list[CheckResult]:
    """Check cluster role configuration is consistent."""
    results: list[CheckResult] = []

    hub_url = getattr(settings, "HUB_URL", "")
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)
    secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")
    instance_id = getattr(settings, "INSTANCE_ID", "")

    if hub_url and cluster_enabled:
        return [CheckResult(
            level="error",
            message="Cluster conflict: both HUB_URL and CLUSTER_ENABLED=1",
            hint="An instance cannot be both agent and hub.",
        )]

    if hub_url:  # agent mode
        if not secret:
            results.append(CheckResult(
                level="warn", message="Agent mode: WEBHOOK_SECRET_CLUSTER is empty",
                hint="Set it for signed payloads.",
            ))
        if not instance_id:
            results.append(CheckResult(
                level="warn", message="Agent mode: INSTANCE_ID is empty",
                hint="Set it to identify this agent.",
            ))

    if cluster_enabled and not hub_url:  # hub mode
        if not secret:
            results.append(CheckResult(
                level="error", message="Hub mode: WEBHOOK_SECRET_CLUSTER is empty",
                hint="Required to verify agent payloads.",
            ))

    if not results:
        role = "agent" if hub_url else ("hub" if cluster_enabled else "standalone")
        results.append(CheckResult(level="ok", message=f"Cluster: {role}"))

    return results

# ---------------------------------------------------------------------------
# Runtime consistency checks (ported from status/runtime_checks.py)
# ---------------------------------------------------------------------------

def check_celery_eager() -> list[CheckResult]:
    """Check Celery eager mode is off in production."""
    if _is_production() and settings.CELERY_TASK_ALWAYS_EAGER:
        return [CheckResult(
            level="warn", message="Celery is in eager mode in production",
            hint="Set CELERY_TASK_ALWAYS_EAGER=0.",
        )]
    return []

def check_metrics_config() -> list[CheckResult]:
    """Check metrics backend vs StatsD config consistency."""
    backend = getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging")
    statsd_host = getattr(settings, "STATSD_HOST", "localhost")

    if backend == "logging" and statsd_host != "localhost":
        return [CheckResult(
            level="info",
            message=f"StatsD host set ({statsd_host}) but backend is 'logging'",
            hint="Set ORCHESTRATION_METRICS_BACKEND=statsd to use it.",
        )]
    if backend == "statsd" and statsd_host == "localhost":
        return [CheckResult(
            level="warn", message="Metrics backend is 'statsd' but STATSD_HOST is localhost",
            hint="Set STATSD_HOST to your StatsD server.",
        )]
    return []

# ---------------------------------------------------------------------------
# Pipeline state checks (ported from status/database_checks.py)
# ---------------------------------------------------------------------------

def check_pipeline_state() -> list[CheckResult]:
    """Check pipeline definitions, channels, and providers."""
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineDefinition

    results: list[CheckResult] = []

    active_defs = PipelineDefinition.objects.filter(is_active=True).count()
    active_channels = NotificationChannel.objects.filter(is_active=True).count()
    active_providers = IntelligenceProvider.objects.filter(is_active=True)

    if active_defs > 0 and settings.CELERY_TASK_ALWAYS_EAGER:
        results.append(CheckResult(
            level="warn",
            message=f"{active_defs} active pipeline(s) but Celery is eager",
            hint="Set CELERY_TASK_ALWAYS_EAGER=0 for async execution.",
        ))

    if active_channels == 0:
        results.append(CheckResult(
            level="warn", message="No active notification channels",
            hint="Add one via Django Admin.",
        ))

    if active_defs == 0:
        results.append(CheckResult(
            level="info", message="No active pipeline definitions",
            hint="Create one via Django Admin or: manage.py setup_instance",
        ))

    fallback = getattr(settings, "ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED", True)
    if active_providers.exists() and not fallback:
        results.append(CheckResult(
            level="info", message="Intelligence fallback is disabled",
            hint="Pipeline will fail if AI provider fails.",
        ))

    if not results:
        results.append(CheckResult(
            level="ok", message=f"{active_defs} pipeline(s), {active_channels} channel(s) active",
        ))

    return results

# ---------------------------------------------------------------------------
# Installation state checks (ported from status/installation_checks.py)
# ---------------------------------------------------------------------------

def check_installation_state(base_dir: Path) -> list[CheckResult]:
    """Check aliases, hooks, cron, directory permissions."""
    results: list[CheckResult] = []
    prod = _is_production()

    if not prod:
        if not _path_exists(base_dir / "bin" / "aliases.sh"):
            results.append(CheckResult(
                level="warn", message="Shell aliases not installed",
                hint="Run: bin/install.sh aliases",
            ))
        if not _path_exists(base_dir / ".git" / "hooks" / "pre-commit"):
            results.append(CheckResult(
                level="warn", message="Pre-commit hooks not installed",
                hint="Run: uv run pre-commit install",
            ))

    if prod and not _check_crontab():
        results.append(CheckResult(
            level="warn", message="No cron jobs configured",
            hint="Run: bin/install.sh cron",
        ))

    logs_dir = getattr(settings, "LOGS_DIR", base_dir / "logs")
    if _path_exists(logs_dir) and not _is_writable(logs_dir):
        results.append(CheckResult(
            level="error", message=f"Logs directory not writable: {logs_dir}",
            hint="Fix permissions: chmod u+w",
        ))

    db_settings = settings.DATABASES.get("default", {})
    if db_settings.get("ENGINE", "").endswith("sqlite3"):
        db_path = Path(str(db_settings.get("NAME", "")))
        if db_path and _path_exists(db_path) and not _is_writable(db_path):
            results.append(CheckResult(
                level="error", message=f"Database file not writable: {db_path}",
                hint="Fix permissions on SQLite file.",
            ))

    if not results:
        results.append(CheckResult(level="ok", message="Installation state OK"))

    return results

# ---------------------------------------------------------------------------
# Deployment checks (ported from bin/lib/health_check.sh)
# ---------------------------------------------------------------------------

def check_deployment(base_dir: Path) -> list[CheckResult]:
    """Check deployment-specific services (Docker or systemd)."""
    deploy_method = os.environ.get("DEPLOY_METHOD", "bare")

    if deploy_method == "docker":
        return _check_docker(base_dir)

    # bare — check if systemd is in use
    if _systemd_unit_exists():
        return _check_systemd()

    return []  # dev mode, no deployment checks needed

def _systemd_unit_exists() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "server-monitoring.service"],
            capture_output=True, text=True, timeout=5,
        )
        return "server-monitoring" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

def _check_docker(base_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    compose_file = base_dir / "deploy" / "docker" / "docker-compose.yml"

    # Docker daemon
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        results.append(CheckResult(level="ok", message="Docker daemon is running"))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return [CheckResult(level="error", message="Docker daemon is not running")]

    # Container health
    for svc in ("redis", "web", "celery"):
        try:
            r = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json", svc],
                capture_output=True, text=True, timeout=10,
            )
            if "running" in r.stdout.lower():
                results.append(CheckResult(level="ok", message=f"{svc} container is running"))
            else:
                results.append(CheckResult(level="error", message=f"{svc} container is not running"))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            results.append(CheckResult(level="error", message=f"Cannot check {svc} container"))

    return results

def _check_systemd() -> list[CheckResult]:
    results: list[CheckResult] = []

    services = [
        ("server-monitoring", "server-monitoring.service"),
        ("server-monitoring-celery", "server-monitoring-celery.service"),
    ]
    for name, unit in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", name],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                results.append(CheckResult(level="ok", message=f"{unit} is active"))
            else:
                results.append(CheckResult(level="error", message=f"{unit} is not active"))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            results.append(CheckResult(level="warn", message=f"Cannot check {unit}"))

    # Redis
    for redis_name in ("redis-server", "redis"):
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", redis_name],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                results.append(CheckResult(level="ok", message="Redis service is active"))
                break
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    else:
        results.append(CheckResult(level="error", message="Redis service is not active"))

    # Gunicorn socket
    socket_path = Path("/run/server-monitoring/gunicorn.sock")
    if socket_path.exists():
        results.append(CheckResult(level="ok", message="Gunicorn socket exists"))
    else:
        results.append(CheckResult(level="warn", message="Gunicorn socket not found"))

    return results

# ---------------------------------------------------------------------------
# Run all checks in order
# ---------------------------------------------------------------------------

def run_all(base_dir: Path) -> list[CheckResult]:
    """Run all preflight checks in order, returning a flat list."""
    results: list[CheckResult] = []

    # Environment
    results.extend(check_python_version())
    results.extend(check_uv_installed())
    results.extend(check_venv_exists(base_dir))
    results.extend(check_env_file_exists(base_dir))
    results.extend(check_project_writable(base_dir))
    results.extend(check_disk_space(base_dir))

    # Database
    results.extend(check_database_connection())
    results.extend(check_pending_migrations())

    # Security
    results.extend(check_debug_mode())
    results.extend(check_secret_key_strength())
    results.extend(check_allowed_hosts())
    results.extend(check_env_file_permissions(base_dir))

    # Config consistency
    results.extend(check_env_consistency(base_dir))
    results.extend(check_cluster_coherence())
    results.extend(check_celery_eager())
    results.extend(check_metrics_config())

    # Pipeline state
    results.extend(check_pipeline_state())

    # Installation state
    results.extend(check_installation_state(base_dir))

    # Deployment
    results.extend(check_deployment(base_dir))

    return results
```

**Step 2: Write tests**

Create `apps/checkers/_tests/preflight/test_checks.py`. Port all tests from:
- `_tests/status/test_env_checks.py`
- `_tests/status/test_cluster_checks.py`
- `_tests/status/test_runtime_checks.py`
- `_tests/status/test_database_checks.py`
- `_tests/status/test_installation_checks.py`

Update all imports to use `apps.checkers.preflight.checks` instead of `apps.checkers.status.*`. Also add new tests for the new check functions:

- `check_python_version()` — mock `sys.version_info`, `sys.prefix`, `sys.base_prefix`
- `check_uv_installed()` — mock `shutil.which`
- `check_venv_exists()` — mock `_path_exists`
- `check_disk_space()` — mock `shutil.disk_usage`
- `check_database_connection()` — mock Django `connections`
- `check_pending_migrations()` — mock `MigrationExecutor`
- `check_secret_key_strength()` — `override_settings`
- `check_allowed_hosts()` — `override_settings`
- `check_env_file_permissions()` — mock `Path.stat`
- `check_deployment()` — mock `subprocess.run`, `os.environ`
- `run_all()` — integration test that it returns a flat list

**Step 3: Run tests, verify all pass**

Run: `uv run pytest apps/checkers/_tests/preflight/test_checks.py -v`

**Step 4: Commit**

```bash
git add apps/checkers/preflight/checks.py apps/checkers/_tests/preflight/test_checks.py
git commit -m "feat(preflight): add all check functions in flat checks module"
```

---

### Task 4: Add JSON-line logger

**Files:**
- Create: `apps/checkers/preflight/logger.py`
- Test: `apps/checkers/_tests/preflight/test_logger.py`

**Step 1: Write the failing test**

```python
"""Tests for preflight JSON-line logger."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from apps.checkers.preflight import CheckResult
from apps.checkers.preflight.logger import log_results


class LogResultsTests(TestCase):
    def test_appends_json_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "checks.log"
            checks = [
                CheckResult(level="ok", message="test passed"),
                CheckResult(level="warn", message="something wrong", hint="fix it"),
            ]
            log_results(checks, log_path)

            content = log_path.read_text().strip()
            data = json.loads(content)
            self.assertIn("timestamp", data)
            self.assertEqual(data["passed"], 1)
            self.assertEqual(data["warnings"], 1)
            self.assertEqual(data["errors"], 0)
            self.assertEqual(len(data["checks"]), 2)

    def test_appends_multiple_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "checks.log"
            checks = [CheckResult(level="ok", message="ok")]
            log_results(checks, log_path)
            log_results(checks, log_path)

            lines = log_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "subdir" / "checks.log"
            log_results([], log_path)
            self.assertTrue(log_path.exists())

    def test_handles_write_error_gracefully(self):
        checks = [CheckResult(level="ok", message="ok")]
        # Write to unwritable path — should not raise
        log_results(checks, Path("/proc/fake/checks.log"))
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/checkers/_tests/preflight/test_logger.py -v`
Expected: FAIL — ImportError

**Step 3: Write implementation**

```python
"""JSON-line logger for preflight results.

Appends one JSON line per run to logs/checks.log.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from apps.checkers.preflight import CheckResult


def log_results(checks: list[CheckResult], log_path: Path) -> None:
    """Append a JSON-line entry for this preflight run."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": sum(1 for c in checks if c.level == "ok"),
        "warnings": sum(1 for c in checks if c.level == "warn"),
        "errors": sum(1 for c in checks if c.level == "error"),
        "checks": [
            {"level": c.level, "message": c.message, "hint": c.hint}
            for c in checks
        ],
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass  # Don't fail preflight because logging failed
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/checkers/_tests/preflight/test_logger.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/checkers/preflight/logger.py apps/checkers/_tests/preflight/test_logger.py
git commit -m "feat(preflight): add JSON-line logger for check results"
```

---

### Task 5: Rewrite preflight management command

**Files:**
- Modify: `apps/checkers/management/commands/preflight.py` (complete rewrite)
- Test: `apps/checkers/_tests/preflight/test_command.py`

**Step 1: Write the failing tests**

```python
"""Tests for the unified preflight management command."""

import json
import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class PreflightCommandTests(TestCase):
    def _call(self, *args, **kwargs):
        out = StringIO()
        err = StringIO()
        call_command("preflight", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_human_output_has_dashboard_and_checks(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("System", output)
        self.assertIn("Role:", output)
        self.assertIn("Checks", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_json_output_valid(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call("--json")
        data = json.loads(output)
        self.assertIn("profile", data)
        self.assertIn("checks", data)
        self.assertIn("summary", data)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_logger_called(self, mock_log, mock_read):
        mock_read.return_value = None
        self._call()
        mock_log.assert_called_once()

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_summary_line(self, mock_log, mock_read):
        mock_read.return_value = None
        output, _ = self._call()
        self.assertIn("passed", output)
        self.assertIn("warning(s)", output)
        self.assertIn("error(s)", output)

    @patch("apps.checkers.preflight.checks._read_file")
    @patch("apps.checkers.preflight.logger.log_results")
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_definitions_shown(self, mock_log, mock_read):
        mock_read.return_value = None
        PipelineDefinition.objects.create(
            name="test-pipe",
            config={"nodes": [{"id": "n1", "type": "notify", "config": {"driver": "slack"}}]},
            is_active=True,
        )
        output, _ = self._call()
        self.assertIn("test-pipe", output)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/checkers/_tests/preflight/test_command.py -v`
Expected: FAIL (old preflight command has different interface)

**Step 3: Rewrite the preflight command**

Overwrite `apps/checkers/management/commands/preflight.py`:

```python
"""
Unified preflight checks — one command, one output, everything visible.

Usage:
    python manage.py preflight          # Dashboard + all checks
    python manage.py preflight --json   # Full JSON for CI
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.checkers.preflight import CheckResult
from apps.checkers.preflight.checks import run_all
from apps.checkers.preflight.dashboard import get_definitions, get_pipeline_state, get_profile
from apps.checkers.preflight.logger import log_results

BASE_DIR = Path(settings.BASE_DIR)
CHECKS_LOG = Path(settings.LOGS_DIR) / "checks.log"


class Command(BaseCommand):
    help = "Run all preflight checks and show system status"
    requires_system_checks: list[str] = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            default=False,
            dest="json_output",
            help="Output as JSON",
        )

    def handle(self, *args, **options):
        json_output = options["json_output"]

        # Dashboard data
        profile = get_profile()
        pipeline = get_pipeline_state()
        definitions = get_definitions()

        # Run all checks
        all_checks = run_all(base_dir=BASE_DIR)

        # Log results
        log_results(all_checks, CHECKS_LOG)

        # Summary
        passed = sum(1 for c in all_checks if c.level == "ok")
        warnings = sum(1 for c in all_checks if c.level == "warn")
        errors = sum(1 for c in all_checks if c.level == "error")

        if json_output:
            self._output_json(profile, pipeline, definitions, all_checks, passed, warnings, errors)
        else:
            self._output_human(profile, definitions, all_checks, passed, warnings, errors)

    def _output_json(self, profile, pipeline, definitions, checks, passed, warnings, errors):
        data = {
            "profile": profile,
            "pipeline": pipeline,
            "definitions": definitions,
            "checks": [
                {"level": c.level, "message": c.message, "hint": c.hint}
                for c in checks
            ],
            "summary": {"passed": passed, "warnings": warnings, "errors": errors},
        }
        self.stdout.write(json.dumps(data, indent=2, default=str))

    def _output_human(self, profile, definitions, checks, passed, warnings, errors):
        self._render_dashboard(profile, definitions)
        self._render_checks(checks)
        self._render_summary(passed, warnings, errors)

    def _render_dashboard(self, profile, definitions):
        self.stdout.write(
            self.style.MIGRATE_HEADING("\n═══ System ══════════════════════════════════\n")
        )

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

        # Pipeline definitions (compact)
        if definitions:
            self.stdout.write("")
            for defn in definitions:
                status = "active" if defn["active"] else "inactive"
                name_line = f"  {defn['name']} ({status})"
                if defn["active"]:
                    self.stdout.write(self.style.SUCCESS(name_line))
                else:
                    self.stdout.write(f"\033[2m{name_line}\033[0m")
                self.stdout.write(f"    {defn['chain']}")

        self.stdout.write("")

    def _render_checks(self, checks):
        self.stdout.write(
            self.style.MIGRATE_HEADING("═══ Checks ══════════════════════════════════\n")
        )

        for check in checks:
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

    def _render_summary(self, passed, warnings, errors):
        total = passed + warnings + errors
        summary = f"  {total} checks: {passed} passed, {warnings} warning(s), {errors} error(s)"
        if errors > 0:
            self.stdout.write(self.style.ERROR(summary))
        elif warnings > 0:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/checkers/_tests/preflight/test_command.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/checkers/management/commands/preflight.py apps/checkers/_tests/preflight/test_command.py
git commit -m "feat(preflight): rewrite as unified check command with dashboard and logging"
```

---

### Task 6: Simplify shell scripts

**Files:**
- Modify: `bin/check_system.sh` (rewrite as thin wrapper)
- Modify: `bin/check_security.sh` (rewrite as thin wrapper)

**Step 1: Rewrite check_system.sh**

```bash
#!/bin/bash
#
# System check script for server-maintanence
# Thin wrapper around: python manage.py preflight
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

uv run python manage.py preflight "$@"
```

**Step 2: Rewrite check_security.sh**

```bash
#!/bin/bash
#
# Security audit for server-maintanence
# Thin wrapper around: python manage.py preflight
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

uv run python manage.py preflight "$@"
```

**Step 3: Verify they work**

Run: `bin/check_system.sh`
Expected: Same output as `uv run python manage.py preflight`

Run: `bin/check_security.sh`
Expected: Same output

**Step 4: Commit**

```bash
git add bin/check_system.sh bin/check_security.sh
git commit -m "refactor(bin): make check scripts thin wrappers around preflight"
```

---

### Task 7: Simplify CLI menu

**Files:**
- Modify: `bin/cli/system.sh`

**Step 1: Rewrite system.sh**

```bash
# Sourced by cli.sh — do not execute directly.

system_menu() {
    show_banner
    echo -e "${BOLD}═══ System & Security ═══${NC}"
    echo ""

    local options=(
        "Run preflight checks"
        "Set production mode"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py preflight"
                ;;
            2)
                confirm_and_run "$SCRIPT_DIR/set_production.sh"
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

The `security_menu()` function is removed — security checks are now part of preflight.

**Step 2: Verify**

Run: `bin/cli.sh system`
Expected: 3 options instead of 5

**Step 3: Commit**

```bash
git add bin/cli/system.sh
git commit -m "refactor(cli): simplify system menu to single preflight option"
```

---

### Task 8: Delete old code

**Files:**
- Delete: `apps/checkers/status/` (entire directory)
- Delete: `apps/checkers/management/commands/system_status.py`
- Delete: `apps/checkers/_tests/status/` (entire directory)
- Delete: `bin/lib/health_check.sh`
- Delete: `bin/lib/security_check.sh`

**Important:** Do NOT delete `apps/checkers/checks.py` (Django system checks with `@register()` — still used by `manage.py check`). Do NOT delete `bin/lib/paths.sh`, `bin/lib/checks.sh`, `bin/lib/colors.sh` etc. — other scripts still use them.

**Step 1: Verify no other imports reference the deleted modules**

Run: `grep -r "from apps.checkers.status" apps/ config/` — should only find test files and system_status.py (all being deleted).

Run: `grep -r "health_check.sh" bin/` — should only find check_system.sh (now rewritten).

Run: `grep -r "security_check.sh" bin/` — should only find check_security.sh (now rewritten).

**Step 2: Delete files**

```bash
rm -rf apps/checkers/status/
rm -rf apps/checkers/_tests/status/
rm apps/checkers/management/commands/system_status.py
rm bin/lib/health_check.sh
rm bin/lib/security_check.sh
```

**Step 3: Run full test suite**

Run: `uv run pytest`
Expected: All tests pass (old status tests gone, new preflight tests pass)

**Step 4: Commit**

```bash
git add -u
git commit -m "refactor: remove old status/, system_status, and shell check libraries"
```

---

### Task 9: Full test suite, coverage, and lint

**Step 1: Run all preflight tests**

Run: `uv run pytest apps/checkers/_tests/preflight/ -v`
Expected: All tests pass.

**Step 2: Run full test suite**

Run: `uv run pytest`
Expected: No regressions.

**Step 3: Check coverage**

Run: `uv run coverage run --branch -m pytest && uv run coverage report --include="apps/checkers/preflight/*,apps/checkers/management/commands/preflight.py" --show-missing`
Expected: 100% branch coverage on all preflight modules.

**Step 4: Run linters**

Run: `uv run black . && uv run ruff check . --fix && uv run mypy .`
Expected: No issues.

**Step 5: Commit if any formatting changes**

```bash
git add -u
git commit -m "style: format unified preflight module"
```