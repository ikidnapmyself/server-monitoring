"""All preflight check functions.

Each function returns list[CheckResult]. Functions are called in order by
the preflight command to produce a flat list of results.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from apps.checkers.preflight import CheckResult

# ---------------------------------------------------------------------------
# Helpers
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


def _parse_env_keys(content: str) -> set[str]:
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
    return set(re.findall(r'os\.environ\.get\(["\']([A-Z_][A-Z0-9_]*)["\']', content))


def _systemd_unit_exists() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "list-unit-files", "server-monitoring.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "server-monitoring" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------


def check_python_version() -> list[CheckResult]:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}"
    if v < (3, 10):
        return [CheckResult(level="error", message=f"Python {version_str} (need >= 3.10)")]
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        return [
            CheckResult(
                level="warn",
                message=f"Python {version_str} — running outside virtualenv",
                hint="Run via: uv run python manage.py preflight",
            )
        ]
    venv_is_project = ".venv" in sys.prefix
    if not venv_is_project:
        return [
            CheckResult(
                level="warn",
                message=f"Python {version_str} — not using project .venv",
                hint="Activate project venv or run via: uv run",
            )
        ]
    return [CheckResult(level="ok", message=f"Python {version_str} (.venv)")]


def check_uv_installed() -> list[CheckResult]:
    if shutil.which("uv"):
        return [CheckResult(level="ok", message="uv is installed")]
    return [
        CheckResult(
            level="warn",
            message="uv is not installed",
            hint="Install: curl -LsSf https://astral.sh/uv/install.sh | sh",
        )
    ]


def check_venv_exists(base_dir: Path) -> list[CheckResult]:
    if _path_exists(base_dir / ".venv"):
        return [CheckResult(level="ok", message=".venv directory found")]
    return [CheckResult(level="warn", message=".venv not found", hint="Run: uv sync")]


def check_env_file_exists(base_dir: Path) -> list[CheckResult]:
    if _path_exists(base_dir / ".env"):
        return [CheckResult(level="ok", message=".env file found")]
    return [
        CheckResult(
            level="error",
            message=".env file not found",
            hint="Copy .env.sample to .env and configure it.",
        )
    ]


def check_project_writable(base_dir: Path) -> list[CheckResult]:
    if _is_writable(base_dir):
        return [CheckResult(level="ok", message="Project directory is writable")]
    return [CheckResult(level="warn", message="Project directory is not writable")]


def check_disk_space(base_dir: Path) -> list[CheckResult]:
    try:
        usage = shutil.disk_usage(base_dir)
        free_gb = usage.free / (1024**3)
        if free_gb >= 1:
            return [CheckResult(level="ok", message=f"Disk space: {free_gb:.1f}GB free")]
        return [CheckResult(level="warn", message=f"Low disk space: {free_gb:.1f}GB free (< 1GB)")]
    except OSError:
        return [CheckResult(level="warn", message="Could not check disk space")]


# ---------------------------------------------------------------------------
# Database checks
# ---------------------------------------------------------------------------


def check_database_connection() -> list[CheckResult]:
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
            results.append(
                CheckResult(
                    level="error",
                    message=f"Cannot connect to database '{alias}'",
                    hint=str(e),
                )
            )
    return results


def check_pending_migrations() -> list[CheckResult]:
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    try:
        conn = connections["default"]
        conn.ensure_connection()
        executor = MigrationExecutor(conn)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            return [
                CheckResult(
                    level="warn",
                    message=f"{len(plan)} pending migration(s)",
                    hint="Run: uv run python manage.py migrate",
                )
            ]
        return [CheckResult(level="ok", message="No pending migrations")]
    except Exception as e:
        return [CheckResult(level="error", message=f"Migration check failed: {e}")]


# ---------------------------------------------------------------------------
# Security checks
# ---------------------------------------------------------------------------


def check_debug_mode() -> list[CheckResult]:
    prod = _is_production()
    if prod and settings.DEBUG:
        return [
            CheckResult(
                level="error",
                message="DEBUG is enabled in production",
                hint="Set DJANGO_DEBUG=0 in .env.",
            )
        ]
    debug_str = "on" if settings.DEBUG else "off"
    return [CheckResult(level="ok", message=f"DEBUG is {debug_str}")]


def check_secret_key_strength() -> list[CheckResult]:
    key = getattr(settings, "SECRET_KEY", "")
    length = len(key)
    if length >= 50:
        return [CheckResult(level="ok", message=f"SECRET_KEY is {length} chars")]
    return [
        CheckResult(
            level="warn",
            message=f"SECRET_KEY is only {length} chars (need >= 50)",
            hint="Generate a stronger key.",
        )
    ]


def check_allowed_hosts() -> list[CheckResult]:
    prod = _is_production()
    if prod and not settings.ALLOWED_HOSTS:
        return [
            CheckResult(
                level="error",
                message="ALLOWED_HOSTS is empty in production",
                hint="Set DJANGO_ALLOWED_HOSTS in .env.",
            )
        ]
    if "*" in settings.ALLOWED_HOSTS:
        return [
            CheckResult(
                level="warn",
                message="ALLOWED_HOSTS contains wildcard '*'",
                hint="Use specific hostnames in production.",
            )
        ]
    return [CheckResult(level="ok", message="ALLOWED_HOSTS configured")]


def check_env_file_permissions(base_dir: Path) -> list[CheckResult]:
    env_path = base_dir / ".env"
    if not _path_exists(env_path):
        return []
    try:
        mode = env_path.stat().st_mode
        if mode & 0o004:
            return [
                CheckResult(
                    level="warn",
                    message=".env file is world-readable",
                    hint="Run: chmod 600 .env",
                )
            ]
        return [CheckResult(level="ok", message=".env file permissions OK")]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Config consistency checks
# ---------------------------------------------------------------------------


def check_env_consistency(base_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []

    env_content = _read_file(base_dir / ".env")
    if env_content is None:
        return []

    sample_content = _read_file(base_dir / ".env.sample")
    if sample_content is None:
        return [
            CheckResult(
                level="warn",
                message=".env.sample not found",
                hint=".env.sample serves as the reference for expected env vars.",
            )
        ]

    settings_content = _read_file(base_dir / "config" / "settings.py")

    env_keys = _parse_env_keys(env_content)
    sample_active, sample_commented = _parse_sample_keys(sample_content)
    all_sample_keys = sample_active | sample_commented
    settings_refs = _parse_settings_env_refs(settings_content) if settings_content else set()

    missing_from_env = sample_active - env_keys
    for key in sorted(missing_from_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} in .env.sample but missing from .env",
                hint="Add it to .env or remove from .env.sample.",
            )
        )

    unknown_in_env = env_keys - all_sample_keys
    for key in sorted(unknown_in_env):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} in .env but not in .env.sample",
                hint="Add it to .env.sample so others know about it.",
            )
        )

    undocumented = settings_refs - all_sample_keys
    for key in sorted(undocumented):
        results.append(
            CheckResult(
                level="warn",
                message=f"{key} in settings.py but not in .env.sample",
                hint="Document it in .env.sample.",
            )
        )

    if settings_refs:
        unreferenced = sample_active - settings_refs
        for key in sorted(unreferenced):
            results.append(
                CheckResult(
                    level="warn",
                    message=f"{key} in .env.sample but unused in settings.py",
                    hint="Remove if unused, or it may be shell-only.",
                )
            )

    if not results:
        results.append(CheckResult(level="ok", message="Env files are consistent"))

    return results


def check_cluster_coherence() -> list[CheckResult]:
    results: list[CheckResult] = []

    hub_url = getattr(settings, "HUB_URL", "")
    cluster_enabled = getattr(settings, "CLUSTER_ENABLED", False)
    secret = getattr(settings, "WEBHOOK_SECRET_CLUSTER", "")
    instance_id = getattr(settings, "INSTANCE_ID", "")

    if hub_url and cluster_enabled:
        return [
            CheckResult(
                level="error",
                message="Cluster conflict: both HUB_URL and CLUSTER_ENABLED=1",
                hint="An instance cannot be both agent and hub.",
            )
        ]

    if hub_url:
        if not secret:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Set it for signed payloads.",
                )
            )
        if not instance_id:
            results.append(
                CheckResult(
                    level="warn",
                    message="Agent mode: INSTANCE_ID is empty",
                    hint="Set it to identify this agent.",
                )
            )

    if cluster_enabled and not hub_url:
        if not secret:
            results.append(
                CheckResult(
                    level="error",
                    message="Hub mode: WEBHOOK_SECRET_CLUSTER is empty",
                    hint="Required to verify agent payloads.",
                )
            )

    if not results:
        role = "agent" if hub_url else ("hub" if cluster_enabled else "standalone")
        results.append(CheckResult(level="ok", message=f"Cluster: {role}"))

    return results


def check_celery_eager() -> list[CheckResult]:
    if _is_production() and settings.CELERY_TASK_ALWAYS_EAGER:
        return [
            CheckResult(
                level="warn",
                message="Celery is in eager mode in production",
                hint="Set CELERY_TASK_ALWAYS_EAGER=0.",
            )
        ]
    return []


def check_metrics_config() -> list[CheckResult]:
    backend = getattr(settings, "ORCHESTRATION_METRICS_BACKEND", "logging")
    statsd_host = getattr(settings, "STATSD_HOST", "localhost")

    if backend == "logging" and statsd_host != "localhost":
        return [
            CheckResult(
                level="info",
                message=f"StatsD host set ({statsd_host}) but backend is 'logging'",
                hint="Set ORCHESTRATION_METRICS_BACKEND=statsd to use it.",
            )
        ]
    if backend == "statsd" and statsd_host == "localhost":
        return [
            CheckResult(
                level="warn",
                message="Metrics backend is 'statsd' but STATSD_HOST is localhost",
                hint="Set STATSD_HOST to your StatsD server.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Pipeline state checks
# ---------------------------------------------------------------------------


def check_pipeline_state() -> list[CheckResult]:
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.models import PipelineDefinition

    results: list[CheckResult] = []

    active_defs = PipelineDefinition.objects.filter(is_active=True).count()
    active_channels = NotificationChannel.objects.filter(is_active=True).count()
    active_providers = IntelligenceProvider.objects.filter(is_active=True)

    if active_defs > 0 and settings.CELERY_TASK_ALWAYS_EAGER:
        results.append(
            CheckResult(
                level="warn",
                message=f"{active_defs} active pipeline(s) but Celery is eager",
                hint="Set CELERY_TASK_ALWAYS_EAGER=0 for async execution.",
            )
        )

    if active_channels == 0:
        results.append(
            CheckResult(
                level="warn",
                message="No active notification channels",
                hint="Add one via Django Admin.",
            )
        )

    if active_defs == 0:
        results.append(
            CheckResult(
                level="info",
                message="No active pipeline definitions",
                hint="Create one via Django Admin or: manage.py setup_instance",
            )
        )

    fallback = getattr(settings, "ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED", True)
    if active_providers.exists() and not fallback:
        results.append(
            CheckResult(
                level="info",
                message="Intelligence fallback is disabled",
                hint="Pipeline will fail if AI provider fails.",
            )
        )

    if not results:
        results.append(
            CheckResult(
                level="ok",
                message=f"{active_defs} pipeline(s), {active_channels} channel(s) active",
            )
        )

    return results


# ---------------------------------------------------------------------------
# Installation state checks
# ---------------------------------------------------------------------------


def check_installation_state(base_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    prod = _is_production()

    if not prod:
        if not _path_exists(base_dir / "bin" / "aliases.sh"):
            results.append(
                CheckResult(
                    level="warn",
                    message="Shell aliases not installed",
                    hint="Run: bin/install.sh aliases",
                )
            )
        if not _path_exists(base_dir / ".git" / "hooks" / "pre-commit"):
            results.append(
                CheckResult(
                    level="warn",
                    message="Pre-commit hooks not installed",
                    hint="Run: uv run pre-commit install",
                )
            )

    if prod and not _check_crontab():
        results.append(
            CheckResult(
                level="warn",
                message="No cron jobs configured",
                hint="Run: bin/install.sh cron",
            )
        )

    logs_dir = getattr(settings, "LOGS_DIR", base_dir / "logs")
    if _path_exists(logs_dir) and not _is_writable(logs_dir):
        results.append(
            CheckResult(
                level="error",
                message=f"Logs directory not writable: {logs_dir}",
                hint="Fix permissions: chmod u+w",
            )
        )

    db_settings = settings.DATABASES.get("default", {})
    if str(db_settings.get("ENGINE", "")).endswith("sqlite3"):
        db_path = Path(str(db_settings.get("NAME", "")))
        if db_path and _path_exists(db_path) and not _is_writable(db_path):
            results.append(
                CheckResult(
                    level="error",
                    message=f"Database file not writable: {db_path}",
                    hint="Fix permissions on SQLite file.",
                )
            )

    if not results:
        results.append(CheckResult(level="ok", message="Installation state OK"))

    return results


# ---------------------------------------------------------------------------
# Deployment checks
# ---------------------------------------------------------------------------


def check_deployment(base_dir: Path) -> list[CheckResult]:
    deploy_method = os.environ.get("DEPLOY_METHOD", "bare")

    if deploy_method == "docker":
        return _check_docker(base_dir)

    if _systemd_unit_exists():
        return _check_systemd()

    return []


def _check_docker(base_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    compose_file = base_dir / "deploy" / "docker" / "docker-compose.yml"

    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        results.append(CheckResult(level="ok", message="Docker daemon is running"))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return [CheckResult(level="error", message="Docker daemon is not running")]

    for svc in ("redis", "web", "celery"):
        try:
            r = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json", svc],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "running" in r.stdout.lower():
                results.append(CheckResult(level="ok", message=f"{svc} container is running"))
            else:
                results.append(
                    CheckResult(level="error", message=f"{svc} container is not running")
                )
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
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                results.append(CheckResult(level="ok", message=f"{unit} is active"))
            else:
                results.append(CheckResult(level="error", message=f"{unit} is not active"))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            results.append(CheckResult(level="warn", message=f"Cannot check {unit}"))

    for redis_name in ("redis-server", "redis"):
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", redis_name],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                results.append(CheckResult(level="ok", message="Redis service is active"))
                break
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    else:
        results.append(CheckResult(level="error", message="Redis service is not active"))

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
