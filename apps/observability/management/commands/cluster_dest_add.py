"""manage.py cluster_dest_add — register a new outbound log destination."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import ClusterDestination
from config.models import APIKey


class Command(BaseCommand):
    help = "Register a new cluster log-push destination."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Admin identifier (unique).")
        parser.add_argument("--url", required=True, help="Hub URL, e.g. https://hub.example.com")
        parser.add_argument(
            "--api-key",
            required=True,
            dest="api_key_name",
            help="Name of an existing APIKey to use as the auth credential.",
        )
        parser.add_argument(
            "--streams",
            default="events,heartbeats",
            help="Comma-separated streams to push (default: events,heartbeats).",
        )
        parser.add_argument(
            "--forward",
            action="store_true",
            dest="forward_received",
            help="Also re-push records received from other agents.",
        )
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        name = options["name"]
        if ClusterDestination.objects.filter(name=name).exists():
            raise CommandError(f"Destination '{name}' already exists.")
        try:
            api_key = APIKey.objects.get(name=options["api_key_name"])
        except APIKey.DoesNotExist:
            raise CommandError(f"No APIKey named '{options['api_key_name']}'.") from None

        dest = ClusterDestination.objects.create(
            name=name,
            hub_url=options["url"],
            api_key=api_key,
            streams=options["streams"],
            forward_received=options["forward_received"],
        )
        if options["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "id": dest.id,
                        "name": dest.name,
                        "hub_url": dest.hub_url,
                        "streams": dest.streams,
                        "forward_received": dest.forward_received,
                    }
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created destination '{name}' -> {dest.hub_url} "
                    f"(streams={dest.streams}, "
                    f"forward_received={dest.forward_received})"
                )
            )
