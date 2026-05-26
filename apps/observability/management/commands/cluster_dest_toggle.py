"""manage.py cluster_dest_toggle --name X — flip is_active."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "Flip the is_active flag on a cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Destination name.")

    def handle(self, *args, **options):
        name = options["name"]
        try:
            dest = ClusterDestination.objects.get(name=name)
        except ClusterDestination.DoesNotExist:
            raise CommandError(f"No destination named '{name}'.")

        dest.is_active = not dest.is_active
        dest.save(update_fields=["is_active", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Destination '{name}' is_active={dest.is_active}."))
