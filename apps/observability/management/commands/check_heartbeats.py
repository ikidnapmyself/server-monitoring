"""Standalone heartbeat freshness check.

Walks ``HEARTBEAT_REGISTRY``, looks up the latest heartbeat record per name
(from ``LOGS_DIR/heartbeats.jsonl(.1)``), and for every entry that is
"never-seen", stale (older than its ``max_age``), or whose last status was
``fail``, dispatches an Alert through the standard alerts pipeline using the
InternalDriver. Repeated stale ticks update the SAME ``Incident`` because the
dedup key is the Alert ``fingerprint`` (``heartbeat-stale:<name>``).

Exit codes:
    0 — every registered heartbeat is fresh + ok.
    1 — at least one heartbeat is stale, never-seen, or last-failed.

Use from cron to surface forgotten-cron / dead-agent conditions as real
notifications instead of relying on silent log scraping.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.alerts.services import AlertOrchestrator
from apps.observability.heartbeat_reader import latest_heartbeats
from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY


class Command(BaseCommand):
    help = "Check heartbeat freshness; emit Alerts for any stale registered job."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit a JSON summary instead of human-readable text.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-job stdout (used in cron). Ignored in --json mode.",
        )

    def handle(self, *args, **options):
        agent_mode = bool(getattr(settings, "HUB_URL", ""))
        latest = latest_heartbeats()
        stale: list[dict] = []
        fresh: list[dict] = []

        for name, spec in HEARTBEAT_REGISTRY.items():
            if spec.agent_only and not agent_mode:
                continue
            rec = latest.get(name)
            if rec is None:
                stale.append(
                    {
                        "name": name,
                        "reason": "never-seen",
                        "max_age_seconds": int(spec.max_age.total_seconds()),
                    }
                )
                continue
            # Match checks.py's H001 policy: a malformed ts is treated as
            # maximally stale rather than crashing the whole run (which would
            # silently skip alerts for every later entry in the registry).
            try:
                ts = datetime.fromisoformat(rec.ts.rstrip("Z")).replace(tzinfo=timezone.utc)
            except ValueError:
                ts = datetime.min.replace(tzinfo=timezone.utc)
            age = datetime.now(tz=timezone.utc) - ts
            if age > spec.max_age:
                stale.append(
                    {
                        "name": name,
                        "reason": "stale",
                        "age_seconds": age.total_seconds(),
                        "max_age_seconds": int(spec.max_age.total_seconds()),
                        "last_seen": rec.ts,
                    }
                )
            elif rec.status == "fail":
                stale.append(
                    {
                        "name": name,
                        "reason": "last-status-fail",
                        "last_seen": rec.ts,
                    }
                )
            else:
                fresh.append({"name": name, "last_seen": rec.ts})

        # Dispatch one Alert per stale entry through the standard pipeline.
        # ``allow_internal=True`` is the explicit opt-in the InternalDriver
        # demands for trusted in-process callers (this command is exactly
        # such a caller). Repeated runs reuse the same fingerprint, so the
        # orchestrator updates the existing Alert + Incident.
        if stale:
            orch = AlertOrchestrator()
            for entry in stale:
                spec = HEARTBEAT_REGISTRY[entry["name"]]
                orch.process_webhook(
                    {
                        "source": "observability",
                        "fingerprint": f"heartbeat-stale:{entry['name']}",
                        "title": f"Heartbeat stale: {entry['name']}",
                        "severity": "warning",
                        "labels": {
                            "job": entry["name"],
                            "max_age_seconds": int(spec.max_age.total_seconds()),
                            "reason": entry["reason"],
                        },
                        "description": spec.desc,
                    },
                    driver="internal",
                    allow_internal=True,
                )

        if options["json_output"]:
            self.stdout.write(json.dumps({"stale": stale, "fresh": fresh}, indent=2))
        elif not options["quiet"]:
            for s in stale:
                self.stdout.write(self.style.WARNING(f"STALE  {s['name']}  ({s['reason']})"))
            for f in fresh:
                self.stdout.write(f"FRESH  {f['name']}")

        if stale:
            sys.exit(1)
