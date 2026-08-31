"""Panels for the Node admin detail page.

Every panel is a plain function returning a plain dataclass, so the whole page
is testable without driving the admin. ``NodeAdmin.render_change_form`` calls
``build_node_overview`` and the change_form template renders the result.

Nothing here writes. Nothing here knows about requests.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.timesince import timesince

from apps.alerts.identity import local_instance_id
from config.dashboard import NODE_RECENT_MINUTES


@dataclass(frozen=True)
class Identity:
    is_local: bool
    role_label: str
    freshness_status: str  # "ok" | "warn"
    freshness_label: str


def build_identity(node) -> Identity:
    """Role and freshness for the header.

    Freshness reuses the dashboard's own window rather than restating a number,
    so a node that reads amber on the dashboard reads amber here.
    """
    is_local = node.instance_id == local_instance_id()
    now = timezone.now()
    cutoff = now - timedelta(minutes=NODE_RECENT_MINUTES)
    status = "ok" if node.last_seen >= cutoff else "warn"
    return Identity(
        is_local=is_local,
        role_label="This hub" if is_local else "Peer",
        freshness_status=status,
        freshness_label=f"{timesince(node.last_seen, now)} ago",
    )
