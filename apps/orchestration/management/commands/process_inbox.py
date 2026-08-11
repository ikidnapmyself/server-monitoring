"""Broker-free inbox drain: process PENDING pipeline runs recorded by the webhook.

Durable ingest records each inbound alert as a PENDING ``PipelineRun``; this command
claims and executes them. Run it as a supervised loop (systemd ``--loop``) or a
one-shot pass from cron. The claim is atomic (PENDING -> PROCESSING) so overlapping
drains never double-process, and a crashed drain's PROCESSING runs are reclaimed
after ``--stale-minutes``.

All claim/drain/reclaim logic lives in ``apps.orchestration.inbox`` so the admin
Inbox monitor can reuse it; this command is a thin CLI wrapper.
"""

import time

from django.core.management.base import BaseCommand, CommandError

from apps.orchestration import inbox
from apps.orchestration.models import PipelineRun


class Command(BaseCommand):
    help = "Drain PENDING pipeline runs (broker-free inbox worker)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Max runs to process per pass.")
        parser.add_argument("--loop", action="store_true", help="Poll forever (supervised drain).")
        parser.add_argument(
            "--interval", type=float, default=5.0, help="Seconds to sleep between passes in --loop."
        )
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=inbox.DEFAULT_STALE_MINUTES,
            help="Reclaim PROCESSING runs older than this (crashed drain recovery).",
        )
        parser.add_argument(
            "--id", dest="run_id", default=None, help="Process one specific PENDING run by run_id."
        )

    def handle(self, *args, **options):
        stale_minutes = options["stale_minutes"]
        limit = options["limit"]

        if options["run_id"]:
            processed = self._drain_one(options["run_id"])
            self.stdout.write(f"Processed {processed} run(s).")
            return

        if options["loop"]:
            self.stdout.write("Draining inbox (loop; Ctrl-C to stop)...")
            try:
                while True:
                    self._reclaim_stale(stale_minutes)
                    self._drain(limit)
                    time.sleep(options["interval"])
            except KeyboardInterrupt:
                self.stdout.write("Stopped.")
            return

        self._reclaim_stale(stale_minutes)
        processed = self._drain(limit)
        self.stdout.write(f"Processed {processed} run(s).")

    def _reclaim_stale(self, stale_minutes: int) -> None:
        """Return PROCESSING runs stuck past the timeout to PENDING for retry (global)."""
        inbox.reclaim_stuck(stale_minutes)

    def _drain(self, limit: int) -> int:
        """Claim and execute up to ``limit`` PENDING runs (oldest first)."""
        return inbox.drain(limit)

    def _drain_one(self, run_id: str) -> int:
        """Process one specific run by run_id (the manual 'process now' escape hatch)."""
        try:
            processed = inbox.drain_run(run_id)
        except PipelineRun.DoesNotExist:
            raise CommandError(f"No pipeline run with run_id={run_id}")
        if processed == 0:
            self.stdout.write(f"Run {run_id} is not PENDING; skipping.")
        return processed
