"""Tests for emit_heartbeat() and heartbeat() context manager.

The shared ``redirect_heartbeat_handler`` fixture (autouse, in
``conftest.py``) rebinds the heartbeat logger's handlers to write into
``tmp_path`` for every test in this package.
"""

import json
from pathlib import Path

import pytest

from apps.observability.heartbeat import emit_heartbeat, heartbeat


def _read_heartbeats(logs_dir: Path) -> list[dict]:
    path = logs_dir / "heartbeats.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_emit_heartbeat_writes_one_record(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    emit_heartbeat("check_health.hourly", status="ok", duration_ms=12.3, metrics={"checks_run": 5})
    recs = _read_heartbeats(tmp_path)
    assert len(recs) == 1
    r = recs[0]
    # The formatter promotes heartbeat fields to top-level; this is the
    # contract that latest_heartbeats() in Task 3.3 depends on.
    assert r["name"] == "check_health.hourly"
    assert r["status"] == "ok"
    assert r["duration_ms"] == 12.3
    assert r["metrics"] == {"checks_run": 5}


def test_emit_heartbeat_never_raises_on_disk_error(monkeypatch, settings, tmp_path):
    settings.LOGS_DIR = tmp_path

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("logging.handlers.RotatingFileHandler.emit", boom)
    # Must not raise
    emit_heartbeat("test.job", status="ok")


def test_heartbeat_ctx_manager_emits_running_then_ok(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    with heartbeat("test.job"):
        pass
    recs = _read_heartbeats(tmp_path)
    assert [r["status"] for r in recs] == ["running", "ok"]
    assert recs[1]["duration_ms"] is not None


def test_heartbeat_ctx_manager_emits_fail_and_reraises(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    with pytest.raises(RuntimeError):
        with heartbeat("test.job"):
            raise RuntimeError("oops")
    recs = _read_heartbeats(tmp_path)
    assert recs[-1]["status"] == "fail"
    assert recs[-1]["metrics"]["error_type"] == "RuntimeError"
