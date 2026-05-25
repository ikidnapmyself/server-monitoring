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


def _iso_to_utc(spec: str) -> datetime:
    # Treat trailing Z as +00:00 (Python 3.11+ accepts Z natively but we
    # target 3.10+). Naive datetimes are stamped UTC; aware datetimes are
    # converted to UTC. Raises ValueError on unparseable input.
    normalised = spec.replace("Z", "+00:00") if spec.endswith("Z") else spec
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_since(spec: str | None) -> datetime | None:
    if not spec:
        return None
    m = _DURATION_RE.match(spec)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.now(tz=timezone.utc) - timedelta(seconds=n * _UNIT_TO_SECONDS[unit])
    return _iso_to_utc(spec)


def _parse_record_ts(obj: dict) -> datetime | None:
    # Resilient parser for a record's `ts` field: returns None on missing,
    # non-string, or unparseable values so a single bad line does not
    # crash the reader.
    raw = obj.get("ts")
    if not isinstance(raw, str):
        return None
    try:
        return _iso_to_utc(raw)
    except ValueError:
        return None


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
        until = _parse_since(self.until)
        if since or until:
            ts = _parse_record_ts(obj)
            if ts is None:
                return False
            if since and ts < since:
                return False
            if until and ts > until:
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
