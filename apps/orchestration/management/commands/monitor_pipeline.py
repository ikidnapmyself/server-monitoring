"""
Management command to monitor pipeline runs and their statuses.

Usage:
    # List recent pipeline runs
    python manage.py monitor_pipeline --limit 10

    # Filter by status
    python manage.py monitor_pipeline --status failed

    # Show details for a specific run
    python manage.py monitor_pipeline --run-id <run_id>
"""

from django.core.management.base import BaseCommand

from apps.orchestration.models import PipelineRun


class Command(BaseCommand):
    help = "Monitor pipeline runs: list, filter, and show details."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Number of pipeline runs to show (default: 10)",
        )
        parser.add_argument(
            "--status",
            type=str,
            help="Filter by pipeline status (pending, ingested, checked, analyzed, notified, failed, retrying, skipped)",
        )
        parser.add_argument(
            "--run-id",
            type=str,
            help="Show details for a specific pipeline run (by run_id)",
        )

    def handle(self, *args, **options):
        run_id = options.get("run_id")
        status = options.get("status")
        limit = options.get("limit")

        if run_id:
            self.show_run_details(run_id)
        else:
            self.list_runs(status, limit)

    def list_runs(self, status, limit):
        qs = PipelineRun.objects.all()
        if status:
            qs = qs.filter(status__iexact=status)
        qs = qs.order_by("-created_at")[:limit]

        if not qs:
            self.stdout.write(self.style.WARNING("No pipeline runs found."))
            return

        self.stdout.write(
            f"{'Run ID':<24} {'Status':<10} {'Trace ID':<24} {'Source':<12} {'Created':<20} {'Duration(ms)':<12}"
        )
        self.stdout.write("-" * 100)
        for run in qs:
            self.stdout.write(
                f"{run.run_id:<24} {run.status:<10} {run.trace_id:<24} {run.source:<12} {run.created_at:%Y-%m-%d %H:%M:%S} {run.total_duration_ms:<12.2f}"
            )

    def show_run_details(self, run_id):
        try:
            run = PipelineRun.objects.get(run_id=run_id)
        except PipelineRun.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Pipeline run not found: {run_id}"))
            return

        self.stdout.write(self.style.HTTP_INFO(f"Pipeline Run: {run.run_id}"))
        self.stdout.write(f"  Status: {run.status}")
        self.stdout.write(f"  Trace ID: {run.trace_id}")
        self.stdout.write(f"  Source: {run.source}")
        self.stdout.write(f"  Environment: {run.environment}")
        self.stdout.write(f"  Created: {run.created_at:%Y-%m-%d %H:%M:%S}")
        self.stdout.write(f"  Started: {run.started_at}")
        self.stdout.write(f"  Completed: {run.completed_at}")
        self.stdout.write(f"  Duration: {run.total_duration_ms:.2f} ms")
        if run.last_error_message:
            self.stdout.write(self.style.ERROR(f"  Last error: {run.last_error_message}"))
        self.stdout.write("")
        self.stdout.write("Stage Executions:")
        stages = run.stage_executions.all().order_by("started_at")
        for stage in stages:
            self.stdout.write(
                f"  - {stage.stage:<10} {stage.status:<10} Attempt: {stage.attempt} Duration: {stage.duration_ms:.2f} ms"
            )
            if stage.error_message:
                self.stdout.write(self.style.ERROR(f"      Error: {stage.error_message}"))
        self.stdout.write("")
