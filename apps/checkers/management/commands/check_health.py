"""
Management command to run server health checks.

Usage:
    python manage.py check_health                    # Run all checks
    python manage.py check_health cpu memory         # Run specific checks
    python manage.py check_health --list             # List available checks
    python manage.py check_health --json             # Output as JSON
    python manage.py check_health --fail-on-warning  # Exit 1 on warning or critical
"""

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus


class Command(BaseCommand):
    help = "Run server health checks and display results"

    def add_arguments(self, parser):
        parser.add_argument(
            "checkers",
            nargs="*",
            type=str,
            help="Specific checkers to run (e.g., cpu memory disk). Runs all if not specified.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all available checkers and exit.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output results as JSON.",
        )
        parser.add_argument(
            "--fail-on-warning",
            action="store_true",
            help="Exit with code 1 if any check returns WARNING or CRITICAL.",
        )
        parser.add_argument(
            "--fail-on-critical",
            action="store_true",
            help="Exit with code 1 only if any check returns CRITICAL.",
        )
        parser.add_argument(
            "--warning-threshold",
            type=float,
            help="Override warning threshold for all checks.",
        )
        parser.add_argument(
            "--critical-threshold",
            type=float,
            help="Override critical threshold for all checks.",
        )
        parser.add_argument(
            "--disk-paths",
            nargs="+",
            help="Disk paths to check (for disk checker).",
        )
        parser.add_argument(
            "--ping-hosts",
            nargs="+",
            help="Hosts to ping (for network checker).",
        )
        parser.add_argument(
            "--processes",
            nargs="+",
            help="Process names to check (for process checker).",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._list_checkers()
            return

        checker_names = options["checkers"] or list(CHECKER_REGISTRY.keys())

        invalid = [name for name in checker_names if name not in CHECKER_REGISTRY]
        if invalid:
            raise CommandError(
                f"Unknown checker(s): {', '.join(invalid)}. "
                f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
            )

        if options["json_output"]:
            self.stderr.write(f"Running checkers: {', '.join(checker_names)}\n")
        else:
            self.stdout.write(f"Running checkers: {', '.join(checker_names)}\n")

        results = []
        for name in checker_names:
            checker_class = CHECKER_REGISTRY[name]
            checker_kwargs = self._build_checker_kwargs(name, options)
            checker = checker_class(**checker_kwargs)
            result = checker.check()
            results.append(result)

        if options["json_output"]:
            self._output_json(results)
        else:
            self._output_text(results)

        exit_code = self._determine_exit_code(results, options)
        if exit_code != 0:
            sys.exit(exit_code)

    def _list_checkers(self):
        self.stdout.write(self.style.SUCCESS("Available checkers:\n"))
        for name, checker_class in CHECKER_REGISTRY.items():
            doc = checker_class.__doc__ or "No description"
            description = doc.strip().split("\n")[0]
            self.stdout.write(f"  {self.style.WARNING(name):20} {description}")
        self.stdout.write("")

    def _build_checker_kwargs(self, name: str, options: dict[str, Any]) -> dict[str, Any]:
        kwargs = {}

        if options["warning_threshold"] is not None:
            kwargs["warning_threshold"] = options["warning_threshold"]
        if options["critical_threshold"] is not None:
            kwargs["critical_threshold"] = options["critical_threshold"]

        if name == "disk" and options["disk_paths"]:
            kwargs["paths"] = options["disk_paths"]
        elif name == "network" and options["ping_hosts"]:
            kwargs["hosts"] = options["ping_hosts"]
        elif name == "process" and options["processes"]:
            kwargs["processes"] = options["processes"]

        return kwargs

    def _output_text(self, results):
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(" SERVER HEALTH CHECK RESULTS"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write("")

        for result in results:
            if result.status == CheckStatus.OK:
                status_str = self.style.SUCCESS(f"[{result.status.value.upper()}]")
            elif result.status == CheckStatus.WARNING:
                status_str = self.style.WARNING(f"[{result.status.value.upper()}]")
            elif result.status == CheckStatus.CRITICAL:
                status_str = self.style.ERROR(f"[{result.status.value.upper()}]")
            else:
                status_str = self.style.NOTICE(f"[{result.status.value.upper()}]")

            self.stdout.write(f"{status_str} {result.checker_name}: {result.message}")

            if result.error:
                self.stdout.write(self.style.ERROR(f"       Error: {result.error}"))

            if result.metrics:
                self._output_metrics(result.metrics)

        self.stdout.write("")
        self.stdout.write("-" * 60)

        ok_count = sum(1 for r in results if r.status == CheckStatus.OK)
        warn_count = sum(1 for r in results if r.status == CheckStatus.WARNING)
        crit_count = sum(1 for r in results if r.status == CheckStatus.CRITICAL)
        unknown_count = sum(1 for r in results if r.status == CheckStatus.UNKNOWN)

        summary = f"Total: {len(results)} | "
        summary += f"OK: {ok_count} | "
        summary += f"Warning: {warn_count} | "
        summary += f"Critical: {crit_count}"
        if unknown_count:
            summary += f" | Unknown: {unknown_count}"

        if crit_count:
            self.stdout.write(self.style.ERROR(summary))
        elif warn_count:
            self.stdout.write(self.style.WARNING(summary))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

        self.stdout.write("")

    def _output_metrics(self, metrics: dict):
        """Print key metrics below the checker result line."""
        indent = "       "

        # Disk analysis checkers: space_hogs, old_files, large_files, recommendations
        for key in ("space_hogs", "old_files", "large_files"):
            items = metrics.get(key)
            if items:
                label = key.replace("_", " ").title()
                self.stdout.write(f"{indent}{label}:")
                for item in items[:10]:
                    size = f"{item['size_mb']:.1f} MB"
                    extra = f" ({item['age_days']}d old)" if "age_days" in item else ""
                    self.stdout.write(f"{indent}  - {item['path']}  {size}{extra}")
                if len(items) > 10:
                    self.stdout.write(f"{indent}  ... and {len(items) - 10} more")

        total = metrics.get("total_recoverable_mb")
        if total is not None:
            self.stdout.write(f"{indent}Total recoverable: {total:.1f} MB")

        recs = metrics.get("recommendations")
        if recs:
            self.stdout.write(f"{indent}Recommendations:")
            for rec in recs:
                self.stdout.write(f"{indent}  - {rec}")

        # Standard checkers: flat key-value pairs (percent, paths, etc.)
        skip = {
            "space_hogs",
            "old_files",
            "large_files",
            "total_recoverable_mb",
            "recommendations",
            "platform",
        }
        flat = {
            k: v for k, v in metrics.items() if k not in skip and not isinstance(v, (list, dict))
        }
        for key, value in flat.items():
            label = key.replace("_", " ")
            if isinstance(value, float):
                self.stdout.write(f"{indent}{label}: {value:.1f}")
            else:
                self.stdout.write(f"{indent}{label}: {value}")

        # Nested dicts (e.g. disk checker's per-path breakdown)
        nested = {k: v for k, v in metrics.items() if k not in skip and isinstance(v, dict)}
        for key, sub in nested.items():
            self.stdout.write(f"{indent}{key}:")
            for sub_key, sub_val in sub.items():
                if isinstance(sub_val, dict):
                    parts = ", ".join(f"{k}: {v}" for k, v in sub_val.items())
                    self.stdout.write(f"{indent}  {sub_key}: {parts}")
                elif isinstance(sub_val, float):
                    self.stdout.write(f"{indent}  {sub_key}: {sub_val:.1f}")
                else:
                    self.stdout.write(f"{indent}  {sub_key}: {sub_val}")

    def _output_json(self, results):
        output = {
            "results": [
                {
                    "checker": r.checker_name,
                    "status": r.status.value,
                    "message": r.message,
                    "metrics": r.metrics,
                    "error": r.error,
                }
                for r in results
            ],
            "summary": {
                "total": len(results),
                "ok": sum(1 for r in results if r.status == CheckStatus.OK),
                "warning": sum(1 for r in results if r.status == CheckStatus.WARNING),
                "critical": sum(1 for r in results if r.status == CheckStatus.CRITICAL),
                "unknown": sum(1 for r in results if r.status == CheckStatus.UNKNOWN),
            },
        }
        self.stdout.write(json.dumps(output, indent=2))

    def _determine_exit_code(self, results, options) -> int:
        has_critical = any(r.status == CheckStatus.CRITICAL for r in results)
        has_warning = any(r.status == CheckStatus.WARNING for r in results)
        has_unknown = any(r.status == CheckStatus.UNKNOWN for r in results)

        if options["fail_on_critical"]:
            return 1 if has_critical else 0

        if options["fail_on_warning"]:
            return 1 if (has_critical or has_warning) else 0

        if has_critical:
            return 2
        if has_unknown:
            return 1
        return 0
