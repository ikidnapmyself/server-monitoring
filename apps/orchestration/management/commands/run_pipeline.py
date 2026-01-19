"""
Management command to run the pipeline end-to-end.

Usage:
    # Run with sample alert payload
    python manage.py run_pipeline --sample

    # Run with custom JSON payload
    python manage.py run_pipeline --payload '{"alerts": [...]}'

    # Run with payload from file
    python manage.py run_pipeline --file alert.json

    # Run with specific source
    python manage.py run_pipeline --sample --source grafana

    # Run checks only (no alert ingestion)
    python manage.py run_pipeline --checks-only

    # Dry run (show what would happen)
    python manage.py run_pipeline --sample --dry-run
"""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.orchestration.orchestrator import PipelineOrchestrator


class Command(BaseCommand):
    help = "Run the full pipeline: alerts → checkers → intelligence → notify"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sample",
            action="store_true",
            help="Use a sample alert payload for testing",
        )
        parser.add_argument(
            "--payload",
            type=str,
            help="JSON payload string",
        )
        parser.add_argument(
            "--file",
            type=str,
            help="Path to JSON file containing payload",
        )
        parser.add_argument(
            "--source",
            type=str,
            default="cli",
            help="Source system (default: cli)",
        )
        parser.add_argument(
            "--environment",
            type=str,
            default="development",
            help="Environment name (default: development)",
        )
        parser.add_argument(
            "--trace-id",
            type=str,
            help="Custom trace ID for correlation",
        )
        parser.add_argument(
            "--checks-only",
            action="store_true",
            help="Run only the checkers stage (skip alert ingestion)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without executing",
        )
        parser.add_argument(
            "--notify-driver",
            type=str,
            default="generic",
            help="Notification driver to use (default: generic)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output result as JSON",
        )

    def handle(self, *args, **options):
        # Build payload
        payload = self._get_payload(options)

        if options["dry_run"]:
            self._show_dry_run(payload, options)
            return

        # Run pipeline
        self.stdout.write(self.style.NOTICE("Starting pipeline..."))
        self.stdout.write(f"  Source: {options['source']}")
        self.stdout.write(f"  Environment: {options['environment']}")
        self.stdout.write("")

        orchestrator = PipelineOrchestrator()

        try:
            result = orchestrator.run_pipeline(
                payload=payload,
                source=options["source"],
                trace_id=options.get("trace_id"),
                environment=options["environment"],
            )

            if options["json"]:
                self.stdout.write(json.dumps(result.to_dict(), indent=2, default=str))
            else:
                self._display_result(result)

        except Exception as e:
            raise CommandError(f"Pipeline failed: {e}")

    def _get_payload(self, options) -> dict:
        """Build the payload from options."""
        inner_payload = {}
        if options["payload"]:
            try:
                inner_payload = json.loads(options["payload"])
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON payload: {e}")
        elif options["file"]:
            try:
                with open(options["file"]) as f:
                    inner_payload = json.load(f)
            except FileNotFoundError:
                raise CommandError(f"File not found: {options['file']}")
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON in file: {e}")
        elif options["sample"]:
            inner_payload = self._get_sample_payload(options["source"])
        elif options["checks_only"]:
            inner_payload = {}
        else:
            raise CommandError("Must specify --sample, --payload, --file, or --checks-only")

        return {
            "payload": inner_payload,
            "driver": options["source"] if options["source"] != "cli" else None,
            "checker_names": None,  # Run all checkers
        }

    def _get_sample_payload(self, source: str) -> dict[str, object]:
        """Generate a sample payload for testing."""
        samples: dict[str, dict[str, object]] = {
            "alertmanager": {
                "version": "4",
                "receiver": "webhook",
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighCPUUsage",
                            "severity": "warning",
                            "instance": "localhost:9090",
                            "job": "node",
                        },
                        "annotations": {
                            "summary": "High CPU usage detected",
                            "description": "CPU usage is above 80% for 5 minutes",
                        },
                        "startsAt": "2024-01-10T10:00:00Z",
                        "fingerprint": "sample-cpu-alert-001",
                    }
                ],
            },
            "grafana": {
                "receiver": "webhook",
                "status": "firing",
                "state": "alerting",
                "title": "Test Alert from Grafana",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "HighMemoryUsage",
                            "severity": "critical",
                        },
                        "annotations": {
                            "summary": "Memory usage critical",
                        },
                        "startsAt": "2024-01-10T10:00:00Z",
                    }
                ],
            },
            "generic": {
                "name": "Test Alert",
                "status": "firing",
                "severity": "warning",
                "description": "This is a test alert from the CLI",
                "labels": {
                    "source": "cli-test",
                    "environment": "development",
                },
            },
        }

        # Default to generic if source not found
        return samples.get(source, samples["generic"])

    def _show_dry_run(self, payload: dict, options: dict):
        """Display what would happen in a dry run."""
        self.stdout.write(self.style.WARNING("=== DRY RUN ==="))
        self.stdout.write("")
        self.stdout.write("Pipeline Configuration:")
        self.stdout.write(f"  Source: {options['source']}")
        self.stdout.write(f"  Environment: {options['environment']}")
        self.stdout.write(f"  Notify Driver: {options['notify_driver']}")
        self.stdout.write("")
        self.stdout.write("Payload:")
        self.stdout.write(json.dumps(payload, indent=2))
        self.stdout.write("")
        self.stdout.write("Pipeline Stages:")
        self.stdout.write("  1. INGEST  - Parse alert payload, create/update Alert + Incident")
        self.stdout.write("  2. CHECK   - Run system diagnostics (CPU, memory, disk, etc.)")
        self.stdout.write("  3. ANALYZE - AI analysis of incident + checker results")
        self.stdout.write("  4. NOTIFY  - Send notification via configured driver")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Use without --dry-run to execute"))

    def _display_result(self, result):
        """Display pipeline result in human-readable format."""
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(self.style.HTTP_INFO("PIPELINE RESULT"))
        self.stdout.write("=" * 60)
        self.stdout.write("")

        # Overall status (print using style wrappers)
        if result.status == "COMPLETED":
            self.stdout.write(self.style.SUCCESS(f"Status: {result.status}"))
        else:
            self.stdout.write(self.style.ERROR(f"Status: {result.status}"))
        self.stdout.write(f"Trace ID: {result.trace_id}")
        self.stdout.write(f"Run ID: {result.run_id}")
        self.stdout.write(f"Duration: {result.total_duration_ms:.2f}ms")
        self.stdout.write("")

        # Stage results
        stages = [
            ("INGEST", result.ingest),
            ("CHECK", result.check),
            ("ANALYZE", result.analyze),
            ("NOTIFY", result.notify),
        ]

        for stage_name, stage_result in stages:
            self.stdout.write(f"--- {stage_name} ---")
            if stage_result:
                stage_dict = (
                    stage_result if isinstance(stage_result, dict) else stage_result.to_dict()
                )

                # Show key info based on stage
                if stage_name == "INGEST":
                    self.stdout.write(f"  Incident ID: {stage_dict.get('incident_id', 'N/A')}")
                    self.stdout.write(f"  Alerts created: {stage_dict.get('alerts_created', 0)}")
                    self.stdout.write(f"  Severity: {stage_dict.get('severity', 'N/A')}")
                elif stage_name == "CHECK":
                    self.stdout.write(f"  Checks run: {stage_dict.get('checks_run', 0)}")
                    self.stdout.write(f"  Passed: {stage_dict.get('checks_passed', 0)}")
                    self.stdout.write(f"  Failed: {stage_dict.get('checks_failed', 0)}")
                elif stage_name == "ANALYZE":
                    self.stdout.write(f"  Summary: {stage_dict.get('summary', 'N/A')[:100]}")
                    self.stdout.write(
                        f"  Probable cause: {stage_dict.get('probable_cause', 'N/A')[:100]}"
                    )
                    self.stdout.write(
                        f"  Recommendations: {len(stage_dict.get('recommendations', []))}"
                    )
                    if stage_dict.get("fallback_used"):
                        self.stdout.write(self.style.WARNING("  (Fallback used - AI unavailable)"))
                elif stage_name == "NOTIFY":
                    self.stdout.write(
                        f"  Channels attempted: {stage_dict.get('channels_attempted', 0)}"
                    )
                    self.stdout.write(f"  Succeeded: {stage_dict.get('channels_succeeded', 0)}")
                    self.stdout.write(f"  Failed: {stage_dict.get('channels_failed', 0)}")

                # Show errors if any
                errors = stage_dict.get("errors", [])
                if errors:
                    self.stdout.write(self.style.ERROR(f"  Errors: {errors}"))

                self.stdout.write(f"  Duration: {stage_dict.get('duration_ms', 0):.2f}ms")
            else:
                self.stdout.write(self.style.WARNING("  (not executed)"))
            self.stdout.write("")

        # Final summary
        if result.status == "COMPLETED":
            self.stdout.write(self.style.SUCCESS("✓ Pipeline completed successfully"))
        else:
            self.stdout.write(self.style.ERROR(f"✗ Pipeline failed: {result.status}"))

            # Prefer structured final_error if available on PipelineResult
            final_error = getattr(result, "final_error", None)
            try:
                err_type = getattr(final_error, "error_type", type(final_error).__name__)
                err_msg = getattr(final_error, "message", str(final_error))
                self.stdout.write(self.style.ERROR(f"  - {err_type}: {err_msg}"))
                stack = getattr(final_error, "stack_trace", None)
                if stack:
                    self.stdout.write(self.style.ERROR(stack))
            except Exception:
                # Fallback: print string representation
                self.stdout.write(self.style.ERROR(str(final_error)))
