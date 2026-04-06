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

        profile = get_profile()
        definitions = get_definitions()

        all_checks = run_all(base_dir=BASE_DIR)

        log_results(all_checks, CHECKS_LOG)

        passed = sum(1 for c in all_checks if c.level in {"ok", "info"})
        warnings = sum(1 for c in all_checks if c.level == "warn")
        errors = sum(1 for c in all_checks if c.level == "error")

        if json_output:
            pipeline = get_pipeline_state()
            self._output_json(profile, pipeline, definitions, all_checks, passed, warnings, errors)
        else:
            self._output_human(profile, definitions, all_checks, passed, warnings, errors)

    def _output_json(self, profile, pipeline, definitions, checks, passed, warnings, errors):
        data = {
            "profile": profile,
            "pipeline": pipeline,
            "definitions": definitions,
            "checks": [{"level": c.level, "message": c.message, "hint": c.hint} for c in checks],
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
