"""Tests for check_heartbeats management command."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command


def _write_hb(tmp_path, name, *, status="ok", age=timedelta(0)):
    ts = (datetime.now(tz=timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    line = json.dumps(
        {
            "ts": ts,
            "v": 1,
            "level": "INFO",
            "logger": "x",
            "msg": "h",
            "instance_id": "test",
            "name": name,
            "status": status,
        }
    )
    (tmp_path / "heartbeats.jsonl").write_text(line + "\n")


def _write_hbs(tmp_path, entries):
    """Write multiple heartbeats to heartbeats.jsonl in one shot."""
    lines = []
    for name, status, age in entries:
        ts = (datetime.now(tz=timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines.append(
            json.dumps(
                {
                    "ts": ts,
                    "v": 1,
                    "level": "INFO",
                    "logger": "x",
                    "msg": "h",
                    "instance_id": "test",
                    "name": name,
                    "status": status,
                }
            )
        )
    (tmp_path / "heartbeats.jsonl").write_text("\n".join(lines) + "\n")


def test_all_fresh_exits_zero(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""  # non-agent mode; only non-agent_only specs are evaluated
    # Write one fresh OK heartbeat per non-agent_only registered name so the
    # freshness command finds nothing stale and never raises SystemExit.
    _write_hbs(
        tmp_path,
        [
            ("check_health.hourly", "ok", timedelta(seconds=10)),
            ("check_health.daily", "ok", timedelta(seconds=10)),
            ("preflight.scheduled", "ok", timedelta(seconds=10)),
        ],
    )
    call_command("check_heartbeats")  # no SystemExit


def test_stale_creates_incident(tmp_path, settings, db):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    # Other registered names also have no recent heartbeat → they emit
    # "never-seen" alerts too. We only assert on the stale hourly one here.
    with pytest.raises(SystemExit) as excinfo:
        call_command("check_heartbeats")
    assert excinfo.value.code == 1

    from apps.alerts.models import Incident

    assert (
        Incident.objects.filter(alerts__fingerprint__startswith="heartbeat-stale:")
        .distinct()
        .exists()
    )


def test_dedup_by_fingerprint(tmp_path, settings, db):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    for _ in range(3):
        try:
            call_command("check_heartbeats")
        except SystemExit:
            pass

    from apps.alerts.models import Alert, Incident

    # Repeated stale ticks update the SAME Alert (deduped on fingerprint+source).
    alert_count = Alert.objects.filter(fingerprint="heartbeat-stale:check_health.hourly").count()
    assert alert_count == 1, "Repeated stale ticks must update one Alert, not create new ones"

    # And only one Incident should have been opened for that fingerprint.
    incident_count = (
        Incident.objects.filter(alerts__fingerprint="heartbeat-stale:check_health.hourly")
        .distinct()
        .count()
    )
    assert incident_count == 1


def test_json_output(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(seconds=10))
    # Other non-agent specs are never-seen → still stale, so the command exits 1.
    try:
        call_command("check_heartbeats", "--json")
    except SystemExit:
        pass
    out = capsys.readouterr().out
    obj = json.loads(out)
    assert "stale" in obj and "fresh" in obj


# --- Coverage extras -----------------------------------------------------------


def test_malformed_ts_treated_as_stale(tmp_path, settings, db):
    """A heartbeat record with a malformed ``ts`` is treated as maximally
    stale (matches checks.py's H001 policy) instead of crashing the run.
    """
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    line = json.dumps(
        {
            "ts": "not-a-real-timestamp",
            "v": 1,
            "level": "INFO",
            "logger": "x",
            "msg": "h",
            "instance_id": "test",
            "name": "check_health.hourly",
            "status": "ok",
        }
    )
    (tmp_path / "heartbeats.jsonl").write_text(line + "\n")

    with pytest.raises(SystemExit) as excinfo:
        call_command("check_heartbeats")
    assert excinfo.value.code == 1

    from apps.alerts.models import Alert

    assert Alert.objects.filter(fingerprint="heartbeat-stale:check_health.hourly").exists()


def test_last_status_fail_triggers_alert(tmp_path, settings, db):
    """A fresh heartbeat with status=fail still emits an Alert (reason=last-status-fail)."""
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    _write_hbs(
        tmp_path,
        [
            ("check_health.hourly", "fail", timedelta(seconds=10)),
            ("check_health.daily", "ok", timedelta(seconds=10)),
            ("preflight.scheduled", "ok", timedelta(seconds=10)),
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        call_command("check_heartbeats")
    assert excinfo.value.code == 1

    from apps.alerts.models import Alert

    alert = Alert.objects.get(fingerprint="heartbeat-stale:check_health.hourly")
    assert alert.labels["reason"] == "last-status-fail"


def test_quiet_suppresses_human_output_but_still_exits_nonzero(tmp_path, settings, capsys, db):
    """``--quiet`` skips the per-job stdout block but still exits 1 when stale."""
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))

    with pytest.raises(SystemExit) as excinfo:
        call_command("check_heartbeats", "--quiet")
    assert excinfo.value.code == 1

    out = capsys.readouterr().out
    assert "STALE" not in out
    assert "FRESH" not in out
