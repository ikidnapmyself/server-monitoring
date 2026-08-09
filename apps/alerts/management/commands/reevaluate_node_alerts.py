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
    help = "Re-evaluate a node's existing open alerts against its current config."

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
            if input("Apply these changes? [y/N] ").strip().lower() != "y":
                self.stdout.write("Aborted.")
                return

        applied = apply_node_alert_reeval(node)
        self.stdout.write(
            self.style.SUCCESS(
                f"Resolved {applied.resolved_count}; changed severity on "
                f"{applied.severity_changed_count}."
            )
        )

    def _print_report(self, report: ReevalReport) -> None:
        if not report.changes:
            self.stdout.write("No open alerts need re-evaluation.")
            return
        for change in report.changes:
            checker = (change.alert.labels or {}).get("checker", "")
            self.stdout.write(
                f"{checker}: {change.old_severity}/{change.old_status} -> "
                f"{change.new_severity}/{change.new_status} ({change.value})"
            )
