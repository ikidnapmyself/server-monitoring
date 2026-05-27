"""Shared helpers for cluster_dest_* management commands."""

from __future__ import annotations

from django.core.management.base import CommandError

from apps.observability.models import ClusterDestination


def get_destination_or_raise(name: str, *, select_api_key: bool = False) -> ClusterDestination:
    """Return the ClusterDestination by name or raise CommandError."""
    qs = ClusterDestination.objects.all()
    if select_api_key:
        qs = qs.select_related("api_key")
    try:
        return qs.get(name=name)
    except ClusterDestination.DoesNotExist:
        raise CommandError(f"No destination named '{name}'.") from None
