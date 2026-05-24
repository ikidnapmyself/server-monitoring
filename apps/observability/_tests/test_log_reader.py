"""Tests for the log_reader filtering library."""

import json
from datetime import datetime, timedelta, timezone

from apps.observability.log_reader import LogFilter, iter_events


def _write(tmp_path, records, fn="events.jsonl"):
    (tmp_path / fn).write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _rec(**fields):
    base = {
        "ts": "2026-05-17T10:00:00.000Z",
        "v": 1,
        "level": "INFO",
        "logger": "apps.alerts.services",
        "msg": "x",
        "instance_id": "h",
        "category": "alerts",
    }
    base.update(fields)
    return base


def test_iter_events_returns_all_records(tmp_path):
    _write(tmp_path, [_rec(), _rec()])
    assert len(list(iter_events(tmp_path, LogFilter()))) == 2


def test_filter_by_level(tmp_path):
    _write(tmp_path, [_rec(level="INFO"), _rec(level="WARNING")])
    f = LogFilter(level="WARNING")
    assert [r["level"] for r in iter_events(tmp_path, f)] == ["WARNING"]


def test_filter_by_category(tmp_path):
    _write(tmp_path, [_rec(category="alerts"), _rec(category="notify")])
    f = LogFilter(category="notify")
    assert [r["category"] for r in iter_events(tmp_path, f)] == ["notify"]


def test_filter_by_trace_id(tmp_path):
    _write(tmp_path, [_rec(trace_id="t1"), _rec(trace_id="t2"), _rec()])
    f = LogFilter(trace_id="t1")
    assert [r["trace_id"] for r in iter_events(tmp_path, f)] == ["t1"]


def test_filter_by_grep(tmp_path):
    _write(tmp_path, [_rec(msg="hello world"), _rec(msg="goodbye")])
    f = LogFilter(grep="hello")
    assert [r["msg"] for r in iter_events(tmp_path, f)] == ["hello world"]


def test_filter_by_since_duration(tmp_path):
    now = datetime.now(tz=timezone.utc)
    old = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    new = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _write(tmp_path, [_rec(ts=old), _rec(ts=new)])
    f = LogFilter(since="1h")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["ts"] == new


def test_last_n_returns_most_recent(tmp_path):
    records = [_rec(ts=f"2026-05-17T10:00:{i:02d}.000Z") for i in range(5)]
    _write(tmp_path, records)
    f = LogFilter(last=2)
    out = list(iter_events(tmp_path, f))
    assert len(out) == 2
    assert out[-1]["ts"] == "2026-05-17T10:00:04.000Z"


def test_includes_rotated_backups(tmp_path):
    _write(tmp_path, [_rec(ts="2026-05-17T09:00:00.000Z")], fn="events.jsonl.1")
    _write(tmp_path, [_rec(ts="2026-05-17T10:00:00.000Z")], fn="events.jsonl")
    out = list(iter_events(tmp_path, LogFilter()))
    assert len(out) == 2


def test_malformed_line_skipped(tmp_path):
    (tmp_path / "events.jsonl").write_text("not-json\n" + json.dumps(_rec()) + "\n")
    out = list(iter_events(tmp_path, LogFilter()))
    assert len(out) == 1


def test_empty_lines_are_skipped(tmp_path):
    (tmp_path / "events.jsonl").write_text("\n" + json.dumps(_rec()) + "\n\n")
    out = list(iter_events(tmp_path, LogFilter()))
    assert len(out) == 1


def test_missing_logs_dir_yields_nothing(tmp_path):
    # No events.jsonl in directory at all.
    out = list(iter_events(tmp_path, LogFilter()))
    assert out == []


def test_parse_since_returns_none_for_empty():
    from apps.observability.log_reader import _parse_since

    assert _parse_since(None) is None
    assert _parse_since("") is None


def test_parse_since_iso_absolute():
    from apps.observability.log_reader import _parse_since

    parsed = _parse_since("2026-05-17T10:00:00Z")
    assert parsed == datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_since_iso_naive_assumed_utc():
    # Naive ISO datetime (no Z, no offset) takes the `tzinfo is None` branch
    # and is treated as UTC. Documents the contract that the reader does not
    # silently shift naive timestamps into local time.
    from apps.observability.log_reader import _parse_since

    parsed = _parse_since("2026-05-17T10:00:00")
    assert parsed == datetime(2026, 5, 17, 10, 0, 0, tzinfo=timezone.utc)


def test_filter_since_iso_absolute(tmp_path):
    _write(
        tmp_path,
        [
            _rec(ts="2026-05-17T09:00:00.000Z"),
            _rec(ts="2026-05-17T11:00:00.000Z"),
        ],
    )
    f = LogFilter(since="2026-05-17T10:00:00Z")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["ts"] == "2026-05-17T11:00:00.000Z"


def test_filter_by_until(tmp_path):
    _write(
        tmp_path,
        [
            _rec(ts="2026-05-17T09:00:00.000Z"),
            _rec(ts="2026-05-17T11:00:00.000Z"),
        ],
    )
    f = LogFilter(until="2026-05-17T10:00:00Z")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["ts"] == "2026-05-17T09:00:00.000Z"


def test_filter_by_logger_substring(tmp_path):
    _write(
        tmp_path,
        [
            _rec(logger="apps.alerts.services"),
            _rec(logger="apps.notify.dispatch"),
        ],
    )
    f = LogFilter(logger="alerts")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["logger"] == "apps.alerts.services"


def test_filter_by_run_id(tmp_path):
    _write(tmp_path, [_rec(run_id="r1"), _rec(run_id="r2")])
    f = LogFilter(run_id="r1")
    out = list(iter_events(tmp_path, f))
    assert [r["run_id"] for r in out] == ["r1"]


def test_filter_by_incident_id(tmp_path):
    _write(tmp_path, [_rec(incident_id=1), _rec(incident_id=2)])
    f = LogFilter(incident_id=2)
    out = list(iter_events(tmp_path, f))
    assert [r["incident_id"] for r in out] == [2]


def test_filter_grep_matches_extra(tmp_path):
    _write(
        tmp_path,
        [
            _rec(msg="x", extra={"reason": "needle-in-extra"}),
            _rec(msg="x", extra={"reason": "haystack"}),
        ],
    )
    f = LogFilter(grep="needle")
    out = list(iter_events(tmp_path, f))
    assert len(out) == 1
    assert out[0]["extra"]["reason"] == "needle-in-extra"


def test_filter_grep_no_match(tmp_path):
    _write(tmp_path, [_rec(msg="hello")])
    f = LogFilter(grep="zzz-no-match")
    out = list(iter_events(tmp_path, f))
    assert out == []


def test_iter_events_no_last_returns_list(tmp_path):
    _write(tmp_path, [_rec(), _rec()])
    result = iter_events(tmp_path, LogFilter())
    assert isinstance(result, list)
    assert len(result) == 2
