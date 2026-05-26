"""manage.py cluster_dest_remove — soft-disable or hard-delete a destination."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "Deactivate (default) or hard-delete a cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Destination name.")
        parser.add_argument(
            "--hard",
            action="store_true",
            help="Delete the row instead of just setting is_active=False.",
        )

    def handle(self, *args, **options):
        name = options["name"]
        try:
            dest = ClusterDestination.objects.get(name=name)
        except ClusterDestination.DoesNotExist:
            raise CommandError(f"No destination named '{name}'.")

        if options["hard"]:
            dest.delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted destination '{name}'."))
            return

        dest.is_active = False
        dest.save(update_fields=["is_active", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Deactivated destination '{name}'."))
