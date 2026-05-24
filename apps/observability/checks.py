"""Django system checks for the observability stack.

W001 fires when LOGS_DIR is not writable by the running process. Heartbeat
freshness checks (H001/H002/H003) are added in Phase 3.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core import checks

from apps.observability.heartbeat_reader import latest_heartbeats
from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY


@checks.register()
def check_logs_dir_writable(app_configs, **kwargs):
    raw_logs_dir = getattr(settings, "LOGS_DIR", "")
    if not raw_logs_dir:
        return []
    logs_dir = Path(raw_logs_dir)
    try:
        if not logs_dir.exists():
            logs_dir.mkdir(parents=True, exist_ok=True)
        ok = os.access(logs_dir, os.W_OK)
    except OSError:
        ok = False
    if not ok:
        return [
            checks.Warning(
                f"LOGS_DIR is not writable: {logs_dir}",
                hint="Either change LOGS_DIR or grant write access to the application user.",
                id="observability.W001",
            )
        ]
    return []


def _is_agent_mode() -> bool:
    return bool(getattr(settings, "HUB_URL", ""))


def _fmt_td(td: timedelta) -> str:
    """Compact human duration: '12h05m', '7m23s', or '45s' for shorter."""
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@checks.register()
def check_heartbeats_fresh(app_configs, **kwargs):
    errs = []
    latest = latest_heartbeats()
    agent_mode = _is_agent_mode()
    for name, spec in HEARTBEAT_REGISTRY.items():
        if spec.agent_only and not agent_mode:
            continue
        rec = latest.get(name)
        if rec is None:
            errs.append(
                checks.Warning(
                    f"heartbeat {name} has never been seen ({spec.desc})",
                    hint=f"Wire `with heartbeat({name!r})` into the corresponding job.",
                    id="observability.H002",
                )
            )
            continue
        # Parse ts
        ts_s = rec.ts.rstrip("Z")
        try:
            ts = datetime.fromisoformat(ts_s).replace(tzinfo=timezone.utc)
        except ValueError:
            ts = datetime.min.replace(tzinfo=timezone.utc)
        age = datetime.now(tz=timezone.utc) - ts
        if age > spec.max_age:
            errs.append(
                checks.Warning(
                    f"heartbeat {name} is {_fmt_td(age)} old (max {_fmt_td(spec.max_age)}) — {spec.desc}",
                    hint="Check the job's cron entry or its last-run logs.",
                    id="observability.H001",
                )
            )
        if rec.status == "fail":
            errs.append(
                checks.Warning(
                    f"heartbeat {name} last status was fail — {spec.desc}",
                    hint="See heartbeats.jsonl for the failure reason.",
                    id="observability.H003",
                )
            )
    return errs
