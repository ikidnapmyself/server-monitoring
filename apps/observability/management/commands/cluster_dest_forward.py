"""manage.py cluster_dest_forward --name X {on|off}."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "Turn forward_received on or off for a cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Destination name.")
        parser.add_argument("state", help="'on' or 'off'.")

    def handle(self, *args, **options):
        state = options["state"]
        if state not in ("on", "off"):
            raise CommandError("forward state must be 'on' or 'off'")

        name = options["name"]
        try:
            dest = ClusterDestination.objects.get(name=name)
        except ClusterDestination.DoesNotExist:
            raise CommandError(f"No destination named '{name}'.")

        dest.forward_received = state == "on"
        dest.save(update_fields=["forward_received", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"Destination '{name}' forward_received={dest.forward_received}.")
        )
