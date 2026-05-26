"""manage.py cluster_dest_show <name> — show one cluster log destination."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import ClusterDestination


class Command(BaseCommand):
    help = "Show one cluster log-push destination by name."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Destination name.")
        parser.add_argument("--json", action="store_true", help="Machine-readable output.")

    def handle(self, *args, **options):
        name = options["name"]
        try:
            dest = ClusterDestination.objects.select_related("api_key").get(name=name)
        except ClusterDestination.DoesNotExist:
            raise CommandError(f"No destination named '{name}'.")

        if options["json"]:
            self.stdout.write(json.dumps(_to_dict(dest)))
            return

        last_at = dest.last_push_at.isoformat() if dest.last_push_at else "—"
        last_status = dest.last_push_status or "—"
        lines = [
            f"name:             {dest.name}",
            f"hub_url:          {dest.hub_url}",
            f"api_key:          {dest.api_key.name}",
            f"streams:          {dest.streams}",
            f"forward_received: {dest.forward_received}",
            f"is_active:        {dest.is_active}",
            f"max_batch_bytes:  {dest.max_batch_bytes}",
            f"last_push_at:     {last_at}",
            f"last_push_status: {last_status}",
            f"created_at:       {dest.created_at.isoformat()}",
            f"updated_at:       {dest.updated_at.isoformat()}",
            "",
            "Recent pushes:",
            "  No pushes yet.",
        ]
        for line in lines:
            self.stdout.write(line)


def _to_dict(d: ClusterDestination) -> dict:
    return {
        "name": d.name,
        "hub_url": d.hub_url,
        "api_key": d.api_key.name,
        "streams": d.streams,
        "forward_received": d.forward_received,
        "is_active": d.is_active,
        "max_batch_bytes": d.max_batch_bytes,
        "last_push_at": d.last_push_at.isoformat() if d.last_push_at else None,
        "last_push_status": d.last_push_status,
        "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(),
        "recent_pushes": [],
    }
