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

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.checkers.status import (
    CheckResult,
    cluster_checks,
    database_checks,
    env_checks,
    installation_checks,
    runtime_checks,
)
from apps.checkers.status.dashboard import get_definitions, get_pipeline_state, get_profile

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

        profile = get_profile()
        pipeline = get_pipeline_state()
        definitions = get_definitions()

        all_checks: list[CheckResult] = []
        all_checks.extend(env_checks.run(base_dir=BASE_DIR))
        all_checks.extend(cluster_checks.run())
        all_checks.extend(runtime_checks.run())
        all_checks.extend(database_checks.run())
        all_checks.extend(installation_checks.run(base_dir=BASE_DIR))

        passed = sum(1 for c in all_checks if c.level == "ok")
        warnings = sum(1 for c in all_checks if c.level == "warn")
        errors = sum(1 for c in all_checks if c.level == "error")

        if json_output:
            self._output_json(profile, pipeline, definitions, all_checks, passed, warnings, errors)
        else:
            self._output_human(
                profile,
                pipeline,
                definitions,
                all_checks,
                passed,
                warnings,
                errors,
                checks_only=checks_only,
                verbose=verbose,
            )

    def _output_json(self, profile, pipeline, definitions, checks, passed, warnings, errors):
        data = {
            "profile": profile,
            "pipeline": pipeline,
            "definitions": definitions,
            "checks": [
                {"level": c.level, "category": c.category, "message": c.message, "hint": c.hint}
                for c in checks
                if c.level != "ok"
            ],
            "summary": {"passed": passed, "warnings": warnings, "errors": errors},
        }
        self.stdout.write(json.dumps(data, indent=2, default=str))

    def _output_human(
        self,
        profile,
        pipeline,
        definitions,
        checks,
        passed,
        warnings,
        errors,
        checks_only=False,
        verbose=False,
    ):
        if not checks_only:
            self._render_profile(profile)
            self._render_pipeline_state(pipeline)
            self._render_definitions(definitions)

        self._render_checks(checks, verbose)
        self._render_summary(passed, warnings, errors)

    def _render_profile(self, profile):
        self.stdout.write(
            self.style.MIGRATE_HEADING("\n═══ System Profile ══════════════════════════\n")
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
        self.stdout.write("")

    def _render_pipeline_state(self, pipeline):
        self.stdout.write(
            self.style.MIGRATE_HEADING("═══ Pipeline State ══════════════════════════\n")
        )

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

    def _render_definitions(self, definitions):
        self.stdout.write(
            self.style.MIGRATE_HEADING("═══ Pipeline Definitions ════════════════════\n")
        )

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
                self.stdout.write(f"\033[2m{name_line}\033[0m")
            self.stdout.write(f"    {defn['chain']}")
        self.stdout.write("")

    def _render_checks(self, checks, verbose):
        self.stdout.write(
            self.style.MIGRATE_HEADING("═══ Consistency ═════════════════════════════\n")
        )

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

    def _render_summary(self, passed, warnings, errors):
        summary = f"  {passed} passed, {warnings} warning(s), {errors} error(s)"
        if errors > 0:
            self.stdout.write(self.style.ERROR(summary))
        elif warnings > 0:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
