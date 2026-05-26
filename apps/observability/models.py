"""Cluster log-forwarding destination registry."""

from __future__ import annotations

from django.db import models


class ClusterDestination(models.Model):
    """One outbound log-push destination this host knows about.

    A host with zero rows is hub-only / standalone (no outbound push).
    A host with one or more rows pushes its local logs (and, if
    ``forward_received=True``, records received from other agents) to each
    listed hub. Loop prevention is structural via the JSONL record's
    ``path`` field — see
    ``docs/plans/2026-05-25-observability-cluster-topology-design.md``.
    """

    name = models.CharField(max_length=64, unique=True)
    hub_url = models.URLField()
    api_key = models.ForeignKey(
        "config_app.APIKey",
        on_delete=models.PROTECT,
        related_name="cluster_destinations",
    )
    streams = models.CharField(
        max_length=128,
        default="events,heartbeats",
        help_text="Comma-separated list of streams to push (e.g. 'events,heartbeats').",
    )
    forward_received = models.BooleanField(
        default=False,
        help_text=(
            "When true, this destination also re-pushes records this host "
            "received from other agents (subject to loop-prevention via the "
            "record's `path` field). Default false: most nodes push only "
            "their own locally-generated logs."
        ),
    )
    is_active = models.BooleanField(default=True)
    max_batch_bytes = models.PositiveIntegerField(default=10 * 1024 * 1024)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_push_status = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text='e.g. "ok", "fail:401", "fail:5xx".',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name
