"""manage.py cluster_dest_list — list registered cluster log destinations."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "List registered cluster log-push destinations."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        rows = list(ClusterDestination.objects.order_by("name"))
        if options["json"]:
            self.stdout.write(json.dumps([_to_dict(r) for r in rows]))
            return
        if not rows:
            self.stdout.write("No destinations registered.")
            return
        header = ("name", "hub_url", "streams", "forward", "active", "last_push_at", "last_status")
        widths = [
            max(len(header[0]), max(len(r.name) for r in rows)),
            max(len(header[1]), max(len(r.hub_url) for r in rows)),
            max(len(header[2]), max(len(r.streams) for r in rows)),
            len(header[3]),
            len(header[4]),
            max(len(header[5]), 19),
            max(len(header[6]), max(len(r.last_push_status or "—") for r in rows)),
        ]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        self.stdout.write(fmt.format(*header))
        for r in rows:
            self.stdout.write(
                fmt.format(
                    r.name,
                    r.hub_url,
                    r.streams,
                    "yes" if r.forward_received else "no",
                    "yes" if r.is_active else "no",
                    r.last_push_at.strftime("%Y-%m-%d %H:%M:%S") if r.last_push_at else "—",
                    r.last_push_status or "—",
                )
            )


def _to_dict(r: ClusterDestination) -> dict:
    return {
        "name": r.name,
        "hub_url": r.hub_url,
        "streams": r.streams,
        "forward_received": r.forward_received,
        "is_active": r.is_active,
        "last_push_at": r.last_push_at.isoformat() if r.last_push_at else None,
        "last_push_status": r.last_push_status or None,
    }
