"""Registry of expected heartbeats.

Operators see freshness alerts only for names in this registry. Adding
a new entry is a code change that ships with the job that emits it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class HeartbeatSpec:
    max_age: timedelta
    desc: str
    agent_only: bool = False


HEARTBEAT_REGISTRY: dict[str, HeartbeatSpec] = {
    "check_health.hourly": HeartbeatSpec(timedelta(minutes=75), "Hourly health-check cron"),
    "check_health.daily": HeartbeatSpec(timedelta(hours=25), "Daily health-check cron"),
    "push_to_hub": HeartbeatSpec(timedelta(minutes=15), "Agent → hub alerts push", agent_only=True),
    "cluster_push.events": HeartbeatSpec(
        timedelta(minutes=15), "Agent → hub log push", agent_only=True
    ),
    "preflight.scheduled": HeartbeatSpec(timedelta(hours=25), "Daily preflight"),
}
