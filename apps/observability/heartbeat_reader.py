"""Scan heartbeats.jsonl (+ most recent rotated backup) for latest record per name."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeartbeatRecord:
    name: str
    ts: str
    status: str
    duration_ms: float | None = None
    metrics: dict | None = None

    @property
    def ts_dt(self) -> datetime:
        # All heartbeat timestamps are emitted as UTC + trailing Z by JsonLineFormatter._utc_iso.
        # Strip the Z and stamp UTC explicitly so consumers get an aware UTC datetime.
        return datetime.fromisoformat(self.ts.rstrip("Z")).replace(tzinfo=timezone.utc)


def latest_heartbeats(logs_dir: Path | None = None) -> dict[str, HeartbeatRecord]:
    base = Path(logs_dir) if logs_dir else Path(settings.LOGS_DIR)
    candidates = [base / "heartbeats.jsonl.1", base / "heartbeats.jsonl"]
    latest: dict[str, HeartbeatRecord] = {}
    for path in candidates:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    _logger.warning("skipping malformed heartbeat line in %s", path)
                    continue
                name = obj.get("name")
                ts = obj.get("ts")
                if not name or not ts:
                    continue
                rec = HeartbeatRecord(
                    name=name,
                    ts=ts,
                    status=obj.get("status", "ok"),
                    duration_ms=obj.get("duration_ms"),
                    metrics=obj.get("metrics"),
                )
                prev = latest.get(name)
                if prev is None or rec.ts > prev.ts:
                    latest[name] = rec
    return latest
