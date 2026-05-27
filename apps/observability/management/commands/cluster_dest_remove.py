"""manage.py cluster_dest_remove — soft-disable or hard-delete a destination."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.observability.management.commands._cluster_dest_common import (
    get_destination_or_raise,
)


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
        dest = get_destination_or_raise(name)

        if options["hard"]:
            dest.delete()
            self.stdout.write(self.style.SUCCESS(f"Deleted destination '{name}'."))
            return

        dest.is_active = False
        dest.save(update_fields=["is_active", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Deactivated destination '{name}'."))
