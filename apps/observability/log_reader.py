"""Parsing + filtering for events.jsonl / heartbeats.jsonl."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

_DURATION_RE = re.compile(r"^(\d+)([smhdw])$")
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def _parse_since(spec: str | None) -> datetime | None:
    if not spec:
        return None
    m = _DURATION_RE.match(spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.now(tz=timezone.utc) - timedelta(seconds=n * _UNIT_TO_SECONDS[unit])
    # ISO-8601 absolute. Treat trailing Z as +00:00 (Python 3.11+ accepts Z
    # natively but we target 3.10+). Otherwise the explicit offset is preserved
    # via fromisoformat. Naive datetimes are assumed UTC.
    normalised = spec.replace("Z", "+00:00") if spec.endswith("Z") else spec
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


@dataclass
class LogFilter:
    category: str | None = None
    level: str | None = None
    logger: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    incident_id: int | None = None
    grep: str | None = None
    since: str | None = None
    until: str | None = None
    last: int | None = None

    def matches(self, obj: dict) -> bool:
        if self.category and obj.get("category") != self.category:
            return False
        if self.level and obj.get("level") != self.level:
            return False
        if self.logger and self.logger not in obj.get("logger", ""):
            return False
        if self.trace_id and obj.get("trace_id") != self.trace_id:
            return False
        if self.run_id and obj.get("run_id") != self.run_id:
            return False
        if self.incident_id is not None and obj.get("incident_id") != self.incident_id:
            return False
        if self.grep:
            haystack = obj.get("msg", "") + " " + json.dumps(obj.get("extra", {}))
            if not re.search(self.grep, haystack):
                return False
        since = _parse_since(self.since)
        ts: datetime | None = None
        if since:
            raw_ts = obj.get("ts")
            if not isinstance(raw_ts, str):
                return False
            normalised = raw_ts.replace("Z", "+00:00") if raw_ts.endswith("Z") else raw_ts
            try:
                ts = datetime.fromisoformat(normalised)
            except ValueError:
                return False
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            if ts < since:
                return False
        until = _parse_since(self.until)
        if until:
            if ts is None:
                raw_ts = obj.get("ts")
                if not isinstance(raw_ts, str):
                    return False
                normalised = raw_ts.replace("Z", "+00:00") if raw_ts.endswith("Z") else raw_ts
                try:
                    ts = datetime.fromisoformat(normalised)
                except ValueError:
                    return False
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
            if ts > until:
                return False
        return True


def _stream_files(logs_dir: Path, basename: str) -> Iterator[dict]:
    """Yield records from rotated backups, then the live file (chronological order)."""
    candidates: list[Path] = []
    # Rotated backups in oldest-first order: .N, .N-1, ..., .1
    backups = sorted(
        logs_dir.glob(f"{basename}.*"),
        key=lambda p: int(p.suffix.lstrip(".")),
        reverse=True,
    )
    candidates.extend(backups)
    live = logs_dir / basename
    if live.exists():
        candidates.append(live)
    for path in candidates:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def iter_events(logs_dir: Path, flt: LogFilter, basename: str = "events.jsonl") -> Iterable[dict]:
    matched = (r for r in _stream_files(logs_dir, basename) if flt.matches(r))
    if flt.last:
        buf: deque[dict] = deque(maxlen=flt.last)
        buf.extend(matched)
        return list(buf)
    return list(matched)
