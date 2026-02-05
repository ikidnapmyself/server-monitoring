"""
Management command to get intelligence recommendations.

Usage:
    python manage.py get_recommendations
    python manage.py get_recommendations --incident-id=1
    python manage.py get_recommendations --memory
    python manage.py get_recommendations --disk --path=/var/log
    python manage.py get_recommendations --all
"""

import json

from django.core.management.base import BaseCommand

from apps.intelligence.providers import get_provider, list_providers


class Command(BaseCommand):
    help = "Get intelligence recommendations based on system state or incidents"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_mode = False

    def _progress(self, msg: str) -> None:
        """Write progress message to stdout, unless in JSON mode."""
        if not self._json_mode:
            self.stdout.write(msg)

    def add_arguments(self, parser):
        parser.add_argument(
            "--incident-id",
            type=int,
            help="Analyze a specific incident by ID",
        )
        parser.add_argument(
            "--provider",
            type=str,
            default="local",
            help="Intelligence provider to use (default: local)",
        )
        parser.add_argument(
            "--memory",
            action="store_true",
            help="Get memory-specific recommendations",
        )
        parser.add_argument(
            "--disk",
            action="store_true",
            help="Get disk-specific recommendations",
        )
        parser.add_argument(
            "--path",
            type=str,
            default="/",
            help="Path to analyze for disk recommendations (default: /)",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=10,
            help="Number of top processes to show (default: 10)",
        )
        parser.add_argument(
            "--threshold-mb",
            type=float,
            default=100.0,
            help="Minimum file size in MB to consider large (default: 100)",
        )
        parser.add_argument(
            "--old-days",
            type=int,
            default=30,
            help="Age in days for old file detection (default: 30)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Get all recommendations (memory + disk)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output as JSON",
        )
        parser.add_argument(
            "--list-providers",
            action="store_true",
            help="List available providers",
        )

    def handle(self, *args, **options):
        # Set json mode flag
        self._json_mode = options.get("json", False)

        # List providers
        if options["list_providers"]:
            providers = list_providers()
            self.stdout.write("Available providers:")
            for name in providers:
                self.stdout.write(f"  - {name}")
            return

        # Get provider
        try:
            provider = get_provider(
                options["provider"],
                progress_callback=self._progress,
                top_n_processes=options["top_n"],
                large_file_threshold_mb=options["threshold_mb"],
                old_file_days=options["old_days"],
            )
        except KeyError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return

        recommendations = []

        # Analyze specific incident
        if options["incident_id"]:
            from apps.alerts.models import Incident

            try:
                incident = Incident.objects.get(id=options["incident_id"])
                recommendations = provider.analyze(incident)
                self.stdout.write(self.style.SUCCESS(f"Analyzing incident: {incident.title}"))
            except Incident.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Incident {options['incident_id']} not found"))
                return

        # Memory-specific analysis
        elif options["memory"]:
            recommendations = provider._get_memory_recommendations()

        # Disk-specific analysis
        elif options["disk"]:
            recommendations = provider._get_disk_recommendations(options["path"])

        # All recommendations
        elif options["all"]:
            recommendations.extend(provider._get_memory_recommendations())
            recommendations.extend(provider._get_disk_recommendations(options["path"]))

        # General recommendations based on current state
        else:
            recommendations = provider.get_recommendations()

        # Output
        if options["json"]:
            output = {
                "provider": options["provider"],
                "recommendations": [r.to_dict() for r in recommendations],
                "count": len(recommendations),
            }
            self.stdout.write(json.dumps(output, indent=2))
        else:
            self._print_recommendations(recommendations)

    def _print_recommendations(self, recommendations):
        """Print recommendations in a human-readable format."""
        if not recommendations:
            self.stdout.write(self.style.SUCCESS("No recommendations at this time."))
            return

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Found {len(recommendations)} recommendation(s):"))
        self.stdout.write("=" * 70)

        for i, rec in enumerate(recommendations, 1):
            # Priority color
            priority_style = {
                "critical": self.style.ERROR,
                "high": self.style.WARNING,
                "medium": self.style.NOTICE,
                "low": self.style.SUCCESS,
            }.get(rec.priority.value, self.style.SUCCESS)

            self.stdout.write("")
            self.stdout.write(f"{i}. [{priority_style(rec.priority.value.upper())}] {rec.title}")
            self.stdout.write(f"   Type: {rec.type.value}")
            self.stdout.write(f"   {rec.description}")

            # Print details if available
            if rec.details:
                if "top_processes" in rec.details:
                    self.stdout.write("")
                    self.stdout.write("   Top Memory Processes:")
                    for proc in rec.details["top_processes"][:5]:
                        self.stdout.write(
                            f"     - {proc['name']} (PID: {proc['pid']}) - "
                            f"{proc['memory_percent']:.1f}% ({proc['memory_mb']:.1f} MB)"
                        )

                if "large_items" in rec.details:
                    self.stdout.write("")
                    self.stdout.write("   Large Files/Directories:")
                    for item in rec.details["large_items"][:5]:
                        item_type = "DIR" if item["is_directory"] else "FILE"
                        self.stdout.write(
                            f"     - [{item_type}] {item['path']} - {item['size_mb']:.1f} MB"
                        )

                if "old_files" in rec.details:
                    self.stdout.write("")
                    self.stdout.write("   Old Files:")
                    for f in rec.details["old_files"][:5]:
                        self.stdout.write(
                            f"     - {f['path']} - {f['size_mb']:.1f} MB ({f['days_old']} days old)"
                        )

            # Print suggested actions
            if rec.actions:
                self.stdout.write("")
                self.stdout.write("   Suggested Actions:")
                for action in rec.actions:
                    self.stdout.write(f"     → {action}")

            self.stdout.write("-" * 70)

        self.stdout.write("")
