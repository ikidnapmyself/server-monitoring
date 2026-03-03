"""
Management command for comprehensive system preflight checks.

Usage:
    python manage.py preflight                    # All checks, human output
    python manage.py preflight --only security    # Filter by tag(s)
    python manage.py preflight --json             # JSON output for CI
    python manage.py preflight --verbosity 0      # Errors only
    python manage.py preflight --verbosity 1      # Errors + warnings (default)
    python manage.py preflight --verbosity 2      # All (errors + warnings + info)

Verbosity can also be set via PREFLIGHT_VERBOSITY in .env (0, 1, or 2).
The --verbosity flag overrides the env var.
"""

import json
import os
from typing import Any

from django.core.checks import Error, Info, run_checks
from django.core.checks import Warning as CheckWarning
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

# Verbosity levels: which check severities to display
# 0 = errors only, 1 = errors + warnings, 2 = all (errors + warnings + info)
LEVEL_PRIORITY = {"error": 0, "warning": 1, "info": 2, "ok": 2}


class Command(BaseCommand):
    help = "Run comprehensive system preflight checks"
    requires_system_checks: list[str] = []

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
        # Configure verbosity default from env var, allowing CLI --verbosity to override.
        parser.set_defaults(verbosity=int(os.environ.get("PREFLIGHT_VERBOSITY", "1")))

    def handle(self, *args, **options):
        only = options.get("only")
        json_output = options.get("json_output", False)

        # Determine verbosity: parser default (from env) overridden by CLI --verbosity
        verbosity = int(options.get("verbosity", 1))

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
            group_warnings = sum(1 for c in checks if isinstance(c, CheckWarning))

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
            self._display_human(results, total_passed, total_warnings, total_errors, verbosity)

    def _display_human(
        self,
        results: dict[str, dict[str, Any]],
        passed: int,
        warnings: int,
        errors: int,
        verbosity: int = 1,
    ) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("=== Preflight Check ===\n"))

        for tag, group in results.items():
            visible = [c for c in group["checks"] if LEVEL_PRIORITY.get(c["level"], 2) <= verbosity]
            if not visible and not group["checks"]:
                self.stdout.write(self.style.MIGRATE_LABEL(group["label"]))
                self.stdout.write("  (no checks registered)")
                self.stdout.write("")
                continue
            if not visible:
                continue

            self.stdout.write(self.style.MIGRATE_LABEL(group["label"]))
            for check in visible:
                level = check["level"]
                msg = check["message"]
                if level == "error":
                    self.stdout.write(self.style.ERROR(f"  ERR  {msg}"))
                elif level == "warning":
                    self.stdout.write(self.style.WARNING(f"  WARN {msg}"))
                elif level == "info":
                    self.stdout.write(f"  \033[34mINFO\033[0m {msg}")
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
    if isinstance(check, CheckWarning):
        return "warning"
    if isinstance(check, Info):
        return "info"
    return "ok"
