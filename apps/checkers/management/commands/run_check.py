"""
Management command to run a single health check.

Usage:
    python manage.py run_check cpu
    python manage.py run_check disk --paths / /data
    python manage.py run_check network --hosts 8.8.8.8 1.1.1.1
    python manage.py run_check process --names nginx postgres
    python manage.py run_check memory --json
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.checkers.checkers import CHECKER_REGISTRY, CheckStatus


class Command(BaseCommand):
    help = "Run a single health check"

    def add_arguments(self, parser):
        parser.add_argument(
            "checker",
            type=str,
            help=f"Checker to run. Options: {', '.join(CHECKER_REGISTRY.keys())}",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output result as JSON.",
        )
        parser.add_argument(
            "--warning-threshold",
            type=float,
            help="Override warning threshold.",
        )
        parser.add_argument(
            "--critical-threshold",
            type=float,
            help="Override critical threshold.",
        )
        # Checker-specific options
        parser.add_argument(
            "--paths",
            nargs="+",
            help="Disk paths to check (disk checker only).",
        )
        parser.add_argument(
            "--hosts",
            nargs="+",
            help="Hosts to ping (network checker only).",
        )
        parser.add_argument(
            "--names",
            nargs="+",
            dest="processes",
            help="Process names to check (process checker only).",
        )
        parser.add_argument(
            "--samples",
            type=int,
            help="Number of CPU samples to take (cpu checker only).",
        )
        parser.add_argument(
            "--sample-interval",
            type=float,
            help="Seconds between CPU samples (cpu checker only).",
        )
        parser.add_argument(
            "--per-cpu",
            action="store_true",
            help="Check per-CPU usage (cpu checker only).",
        )
        parser.add_argument(
            "--include-swap",
            action="store_true",
            help="Include swap memory (memory checker only).",
        )

    def handle(self, *args, **options):
        checker_name = options["checker"]

        if checker_name not in CHECKER_REGISTRY:
            raise CommandError(
                f"Unknown checker: {checker_name}. "
                f"Available: {', '.join(CHECKER_REGISTRY.keys())}"
            )

        # Build kwargs
        kwargs = {}
        if options["warning_threshold"] is not None:
            kwargs["warning_threshold"] = options["warning_threshold"]
        if options["critical_threshold"] is not None:
            kwargs["critical_threshold"] = options["critical_threshold"]

        # Checker-specific options
        if checker_name == "cpu":
            if options.get("samples"):
                kwargs["samples"] = options["samples"]
            if options.get("sample_interval"):
                kwargs["sample_interval"] = options["sample_interval"]
            if options.get("per_cpu"):
                kwargs["per_cpu"] = True
        elif checker_name == "memory":
            if options.get("include_swap"):
                kwargs["include_swap"] = True
        elif checker_name == "disk":
            if options.get("paths"):
                kwargs["paths"] = options["paths"]
        elif checker_name == "network":
            if options.get("hosts"):
                kwargs["hosts"] = options["hosts"]
        elif checker_name == "process":
            if options.get("processes"):
                kwargs["processes"] = options["processes"]

        # Run the check
        checker_class = CHECKER_REGISTRY[checker_name]
        checker = checker_class(**kwargs)
        result = checker.run()

        # Output
        if options["json_output"]:
            output = {
                "checker": result.checker_name,
                "status": result.status.value,
                "message": result.message,
                "metrics": result.metrics,
                "error": result.error,
            }
            self.stdout.write(json.dumps(output, indent=2))
        else:
            self._output_text(result)

    def _output_text(self, result):
        """Output result as formatted text."""
        # Status styling
        if result.status == CheckStatus.OK:
            status_style = self.style.SUCCESS
        elif result.status == CheckStatus.WARNING:
            status_style = self.style.WARNING
        elif result.status == CheckStatus.CRITICAL:
            status_style = self.style.ERROR
        else:
            status_style = self.style.NOTICE

        self.stdout.write("")
        self.stdout.write(status_style(f"[{result.status.value.upper()}] {result.checker_name}"))
        self.stdout.write(f"  {result.message}")

        if result.error:
            self.stdout.write(self.style.ERROR(f"  Error: {result.error}"))

        # Show key metrics
        if result.metrics:
            self.stdout.write("")
            self.stdout.write("  Metrics:")
            for key, value in result.metrics.items():
                if isinstance(value, dict):
                    self.stdout.write(f"    {key}:")
                    for k, v in value.items():
                        self.stdout.write(f"      {k}: {v}")
                else:
                    self.stdout.write(f"    {key}: {value}")

        self.stdout.write("")
