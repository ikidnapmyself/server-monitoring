"""Tests for latest_heartbeats()."""

import json
from pathlib import Path

from apps.observability.heartbeat_reader import HeartbeatRecord, latest_heartbeats


def _write(path: Path, records: list[dict]):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_empty_file_returns_empty_dict(tmp_path):
    (tmp_path / "heartbeats.jsonl").write_text("")
    assert latest_heartbeats(tmp_path) == {}


def test_missing_file_returns_empty_dict(tmp_path):
    assert latest_heartbeats(tmp_path) == {}


def test_latest_per_name_wins(tmp_path):
    _write(
        tmp_path / "heartbeats.jsonl",
        [
            {
                "ts": "2026-05-17T10:00:00Z",
                "name": "job.a",
                "status": "ok",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            },
            {
                "ts": "2026-05-17T11:00:00Z",
                "name": "job.a",
                "status": "fail",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            },
            {
                "ts": "2026-05-17T10:30:00Z",
                "name": "job.b",
                "status": "ok",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            },
        ],
    )
    latest = latest_heartbeats(tmp_path)
    assert latest["job.a"].status == "fail"
    assert latest["job.b"].status == "ok"


def test_reader_includes_rotated_backup(tmp_path):
    _write(
        tmp_path / "heartbeats.jsonl.1",
        [
            {
                "ts": "2026-05-17T09:00:00Z",
                "name": "job.c",
                "status": "ok",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            },
        ],
    )
    _write(tmp_path / "heartbeats.jsonl", [])
    latest = latest_heartbeats(tmp_path)
    assert latest["job.c"].status == "ok"


def test_malformed_line_skipped(tmp_path):
    p = tmp_path / "heartbeats.jsonl"
    p.write_text(
        "not-json\n"
        + json.dumps(
            {
                "ts": "2026-05-17T10:00:00Z",
                "name": "x",
                "status": "ok",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            }
        )
        + "\n"
    )
    latest = latest_heartbeats(tmp_path)
    assert "x" in latest


def test_record_missing_name_or_ts_is_skipped(tmp_path):
    """Coverage: records without 'name' or 'ts' fields are dropped."""
    _write(
        tmp_path / "heartbeats.jsonl",
        [
            {"ts": "2026-05-17T10:00:00Z", "status": "ok"},  # missing name
            {"name": "job.nots", "status": "ok"},  # missing ts
            {
                "ts": "2026-05-17T10:00:00Z",
                "name": "job.ok",
                "status": "ok",
            },
        ],
    )
    latest = latest_heartbeats(tmp_path)
    assert list(latest.keys()) == ["job.ok"]
    assert isinstance(latest["job.ok"], HeartbeatRecord)


def test_default_logs_dir_used_when_none(tmp_path, settings):
    """Coverage: when logs_dir is None, fall back to settings.LOGS_DIR."""
    settings.LOGS_DIR = str(tmp_path)
    _write(
        tmp_path / "heartbeats.jsonl",
        [
            {
                "ts": "2026-05-17T10:00:00Z",
                "name": "job.default",
                "status": "ok",
            }
        ],
    )
    latest = latest_heartbeats()
    assert "job.default" in latest


def test_older_record_after_newer_is_ignored(tmp_path):
    """Coverage: branch where prev exists and rec.ts <= prev.ts (the false branch of line 60)."""
    _write(
        tmp_path / "heartbeats.jsonl",
        [
            {"ts": "2026-05-17T11:00:00Z", "name": "job.x", "status": "fail"},
            {"ts": "2026-05-17T10:00:00Z", "name": "job.x", "status": "ok"},
        ],
    )
    latest = latest_heartbeats(tmp_path)
    # The newer record (status=fail) stays as the latest.
    assert latest["job.x"].status == "fail"


def test_blank_line_is_skipped(tmp_path):
    """Coverage: empty/whitespace-only line skip branch."""
    p = tmp_path / "heartbeats.jsonl"
    p.write_text(
        "\n"
        + json.dumps({"ts": "2026-05-17T10:00:00Z", "name": "job.blank", "status": "ok"})
        + "\n"
    )
    latest = latest_heartbeats(tmp_path)
    assert "job.blank" in latest


def test_ts_dt_returns_datetime():
    """HeartbeatRecord.ts_dt must return an aware UTC datetime whose wall-clock
    matches the input (regardless of the host's local timezone)."""
    from datetime import datetime, timezone

    rec = HeartbeatRecord(name="x", ts="2026-05-17T10:00:00Z", status="ok")
    parsed = rec.ts_dt
    assert isinstance(parsed, datetime)
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)
    # And the wall-clock matches the UTC input
    assert parsed.year == 2026
    assert parsed.month == 5
    assert parsed.day == 17
    assert parsed.hour == 10
    assert parsed.minute == 0


def test_record_with_no_metrics_is_hashable(tmp_path):
    """HeartbeatRecord must stay hashable so it can be used in sets / as dict keys
    even though the dataclass is frozen. Storing `{}` (the prior behaviour) would
    silently make the record unhashable."""
    (tmp_path / "heartbeats.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-17T10:00:00Z",
                "name": "j",
                "status": "ok",
                "level": "INFO",
                "v": 1,
                "logger": "h",
                "msg": "h",
                "instance_id": "x",
            }
        )
        + "\n"
    )
    latest = latest_heartbeats(tmp_path)
    # No `metrics` in input → dataclass default `None` preserved → hashable
    hash(latest["j"])  # must not raise
    assert latest["j"].metrics is None
