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
    results: list[CheckResult] = []
    prod = _is_production()

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
    engine = str(db_settings.get("ENGINE", ""))
    if engine.endswith("sqlite3"):
        db_path = Path(str(db_settings.get("NAME", "")))
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
            CheckResult(
                level="ok",
                message="Installation state is consistent",
                category=CATEGORY,
            )
        )

    return results
