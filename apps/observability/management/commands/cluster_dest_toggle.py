"""manage.py cluster_dest_toggle --name X — flip is_active."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.observability.management.commands._cluster_dest_common import (
    get_destination_or_raise,
)


class Command(BaseCommand):
    help = "Flip the is_active flag on a cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Destination name.")

    def handle(self, *args, **options):
        name = options["name"]
        dest = get_destination_or_raise(name)
        dest.is_active = not dest.is_active
        dest.save(update_fields=["is_active", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Destination '{name}' is_active={dest.is_active}."))
