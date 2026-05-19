"""Tests for emit_heartbeat() and heartbeat() context manager."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from apps.observability.formatter import JsonLineFormatter
from apps.observability.heartbeat import emit_heartbeat, heartbeat


@pytest.fixture(autouse=True)
def redirect_heartbeat_handler(tmp_path, settings):
    """Swap the heartbeat logger's handlers to write into tmp_path.

    The LOGGING config wires `apps.observability.heartbeat` to a
    RotatingFileHandler whose `filename` is captured at startup. Just
    setting `settings.LOGS_DIR = tmp_path` won't redirect that handler,
    so we replace the logger's handlers with a fresh handler bound to
    `tmp_path / heartbeats.jsonl` for the duration of the test.
    """
    settings.LOGS_DIR = tmp_path
    handler = RotatingFileHandler(
        tmp_path / "heartbeats.jsonl",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())
    logger = logging.getLogger("apps.observability.heartbeat")
    old_handlers = logger.handlers[:]
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.propagate = False
    try:
        yield
    finally:
        logger.handlers = old_handlers
        logger.propagate = old_propagate
        handler.close()


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
