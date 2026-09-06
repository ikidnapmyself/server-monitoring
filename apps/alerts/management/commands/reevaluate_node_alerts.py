"""Re-evaluate a node's existing open alerts against its current Node.config.

Usage:
    python manage.py reevaluate_node_alerts web-03             # preview + prompt
    python manage.py reevaluate_node_alerts web-03 --dry-run   # preview only
    python manage.py reevaluate_node_alerts web-03 --noinput   # apply, no prompt
"""

from django.core.management.base import BaseCommand, CommandError

from apps.alerts.models import Node
from apps.alerts.reeval_existing import (
    ReevalReport,
    apply_node_alert_reeval,
    preview_node_alert_reeval,
)


class Command(BaseCommand):
    help = (
        "Re-evaluate a node's existing open alerts against its current config. "
        "Applying enqueues one pipeline run per changed incident, which notifies "
        "once process_inbox drains it, if its lane has a channel. Use --dry-run "
        "to preview without writing or enqueueing anything."
    )

    def add_arguments(self, parser):
        parser.add_argument("instance_id")
        parser.add_argument("--dry-run", action="store_true", help="Preview only.")
        parser.add_argument("--noinput", action="store_true", help="Apply without prompting.")

    def handle(self, *args, **options):
        node = Node.objects.filter(instance_id=options["instance_id"]).first()
        if node is None:
            raise CommandError(f"No node with instance_id '{options['instance_id']}'")

        report = preview_node_alert_reeval(node)
        self._print_report(report)

        if options["dry_run"] or not report.changes:
            return

        if not options["noinput"]:
            try:
                answer = input("Apply these changes? [y/N] ")
            except EOFError:
                # Closed / non-interactive stdin (cron, CI, `echo | ...`): abort safely.
                answer = ""
            if answer.strip().lower() != "y":
                self.stdout.write("Aborted.")
                return

        applied = apply_node_alert_reeval(node)
        self.stdout.write(
            self.style.SUCCESS(
                f"Resolved {applied.resolved_count}; changed severity on "
                f"{applied.severity_changed_count}. Enqueued {applied.run_count} "
                "pipeline run(s)."
            )
        )

    def _print_report(self, report: ReevalReport) -> None:
        for change in report.changes:
            checker = (change.alert.labels or {}).get("checker", "")
            self.stdout.write(
                f"{checker}: {change.old_severity}/{change.old_status} -> "
                f"{change.new_severity}/{change.new_status} ({change.value_display})"
            )
        for skip in report.skips:
            checker = (skip.alert.labels or {}).get("checker", "")
            self.stdout.write(f"skipped {checker}: {skip.sentence}")
        if not report.changes:
            self.stdout.write("No open alerts need re-evaluation.")
            return
        self.stdout.write(
            f"Applying will create {report.run_count} pipeline run(s) for the changes above, "
            "which notify once the inbox drains, if their lane has a channel."
        )
        if report.resolved_count:
            self.stdout.write(
                "Resolving anything also sweeps this node for incidents whose alerts have all "
                "cleared, including ones no change above touched. Each of those is resolved "
                "and gets its own run too."
            )
        if report.reopened_count:
            self.stdout.write(f"Applying will re-open {report.reopened_count} resolved alert(s).")
