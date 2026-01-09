"""
Management command to run health checks and create alerts.

This command runs system health checks and converts the results into alerts,
optionally creating incidents for critical issues.

Usage:
    # Run all checks and create alerts
    python manage.py check_and_alert

    # Run specific checks
    python manage.py check_and_alert --checkers cpu memory disk

    # Dry run (show what would be created)
    python manage.py check_and_alert --dry-run

    # Skip incident creation
    python manage.py check_and_alert --no-incidents

    # Output as JSON
    python manage.py check_and_alert --json
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.alerts.check_integration import CheckAlertBridge
from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus


class Command(BaseCommand):
    help = "Run health checks and create alerts from the results"

    def add_arguments(self, parser):
        parser.add_argument(
            "--checkers",
            nargs="+",
            help=f"Specific checkers to run. Available: {', '.join(CHECKER_REGISTRY.keys())}",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output result as JSON.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without actually creating alerts.",
        )
        parser.add_argument(
            "--no-incidents",
            action="store_true",
            help="Don't automatically create incidents for critical alerts.",
        )
        parser.add_argument(
            "--hostname",
            type=str,
            help="Override hostname in alert labels.",
        )
        parser.add_argument(
            "--label",
            action="append",
            dest="labels",
            metavar="KEY=VALUE",
            help="Additional label to add to all alerts (can be specified multiple times).",
        )
        # Threshold overrides for all checkers
        parser.add_argument(
            "--warning-threshold",
            type=float,
            help="Override warning threshold for all checkers.",
        )
        parser.add_argument(
            "--critical-threshold",
            type=float,
            help="Override critical threshold for all checkers.",
        )

    def handle(self, *args, **options):
        # Parse labels
        labels = {}
        if options.get("labels"):
            for label in options["labels"]:
                if "=" not in label:
                    raise CommandError(f"Invalid label format: {label}. Use KEY=VALUE.")
                key, value = label.split("=", 1)
                labels[key] = value

        # Determine which checkers to run
        checker_names = options.get("checkers") or list(CHECKER_REGISTRY.keys())

        # Validate checker names
        for name in checker_names:
            if name not in CHECKER_REGISTRY:
                raise CommandError(
                    f"Unknown checker: {name}. " f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
                )

        # Build checker configs with threshold overrides
        checker_configs = {}
        for name in checker_names:
            config = {}
            if options.get("warning_threshold") is not None:
                config["warning_threshold"] = options["warning_threshold"]
            if options.get("critical_threshold") is not None:
                config["critical_threshold"] = options["critical_threshold"]
            if config:
                checker_configs[name] = config

        if options["dry_run"]:
            self._dry_run(checker_names, checker_configs, labels, options)
        else:
            self._run_and_alert(checker_names, checker_configs, labels, options)

    def _dry_run(self, checker_names, checker_configs, labels, options):
        """Run checks without creating alerts, show what would be created."""
        self.stdout.write(self.style.NOTICE("DRY RUN - No alerts will be created\n"))

        results = []
        for checker_name in checker_names:
            checker_class = CHECKER_REGISTRY[checker_name]
            config = checker_configs.get(checker_name, {})
            checker = checker_class(**config)

            try:
                result = checker.check()
                results.append(
                    {
                        "checker": checker_name,
                        "status": result.status.value,
                        "message": result.message,
                        "metrics": result.metrics,
                        "would_create_alert": result.status
                        in (
                            CheckStatus.CRITICAL,
                            CheckStatus.WARNING,
                        ),
                    }
                )

                # Format output
                status_style = self._get_status_style(result.status)
                self.stdout.write(
                    f"\n{checker_name.upper()}: " f"{status_style(result.status.value.upper())}"
                )
                self.stdout.write(f"  Message: {result.message}")
                if result.metrics:
                    self.stdout.write(f"  Metrics: {result.metrics}")
                if result.status in (CheckStatus.CRITICAL, CheckStatus.WARNING):
                    self.stdout.write(self.style.WARNING("  → Would create/update alert"))
                else:
                    self.stdout.write(self.style.SUCCESS("  → Would resolve alert (if exists)"))

            except Exception as e:
                results.append(
                    {
                        "checker": checker_name,
                        "error": str(e),
                    }
                )
                self.stdout.write(f"\n{checker_name.upper()}: " f"{self.style.ERROR('ERROR')}")
                self.stdout.write(f"  Error: {e}")

        if options["json_output"]:
            self.stdout.write("\n" + json.dumps({"dry_run": True, "results": results}, indent=2))

    def _run_and_alert(self, checker_names, checker_configs, labels, options):
        """Run checks and create alerts."""
        bridge = CheckAlertBridge(
            auto_create_incidents=not options["no_incidents"],
            hostname=options.get("hostname"),
        )

        result = bridge.run_checks_and_alert(
            checker_names=checker_names,
            checker_configs=checker_configs,
            labels=labels if labels else None,
        )

        if options["json_output"]:
            output = {
                "checks_run": result.checks_run,
                "alerts_created": result.alerts_created,
                "alerts_updated": result.alerts_updated,
                "alerts_resolved": result.alerts_resolved,
                "incidents_created": result.incidents_created,
                "incidents_updated": result.incidents_updated,
                "errors": result.errors,
            }
            self.stdout.write(json.dumps(output, indent=2))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nChecks run: {result.checks_run}"))
            self.stdout.write(f"Alerts created: {result.alerts_created}")
            self.stdout.write(f"Alerts updated: {result.alerts_updated}")
            self.stdout.write(f"Alerts resolved: {result.alerts_resolved}")
            self.stdout.write(f"Incidents created: {result.incidents_created}")
            self.stdout.write(f"Incidents updated: {result.incidents_updated}")

            if result.errors:
                self.stdout.write(self.style.ERROR(f"\nErrors: {len(result.errors)}"))
                for error in result.errors:
                    self.stdout.write(self.style.ERROR(f"  - {error}"))

    def _get_status_style(self, status):
        """Get the appropriate style for a status."""
        if status == CheckStatus.CRITICAL:
            return self.style.ERROR
        elif status == CheckStatus.WARNING:
            return self.style.WARNING
        elif status == CheckStatus.OK:
            return self.style.SUCCESS
        else:
            return self.style.NOTICE
