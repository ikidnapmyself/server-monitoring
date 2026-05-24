"""Django system checks for apps.observability."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def test_w001_passes_when_logs_dir_is_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from apps.observability.checks import check_logs_dir_writable

    errs = check_logs_dir_writable(None)
    assert errs == []


def test_w001_fails_when_logs_dir_is_not_writable(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    # nosec B103: intentional read-only mode on isolated tmp_path to exercise
    # the W001 non-writable branch; restored in the finally clause below.
    os.chmod(tmp_path, 0o555)  # nosec B103
    try:
        from apps.observability.checks import check_logs_dir_writable

        errs = check_logs_dir_writable(None)
        assert any(e.id == "observability.W001" for e in errs)
    finally:
        # Owner-only mode is enough for pytest's tmp_path cleanup.
        os.chmod(tmp_path, 0o700)  # nosec B103


def test_w001_short_circuits_when_logs_dir_is_falsy(settings):
    """Guard branch: if Path() evaluates falsy, return [] without touching disk."""
    from apps.observability import checks as obs_checks

    with patch.object(obs_checks, "Path") as mock_path:
        mock_path.return_value = False  # force `if not logs_dir:` to be True
        settings.LOGS_DIR = ""
        errs = obs_checks.check_logs_dir_writable(None)
    assert errs == []


def test_w001_creates_logs_dir_when_missing(tmp_path, settings):
    """Covers the `mkdir` branch when LOGS_DIR does not yet exist."""
    target = tmp_path / "nested" / "logs"
    assert not target.exists()
    settings.LOGS_DIR = target
    from apps.observability.checks import check_logs_dir_writable

    errs = check_logs_dir_writable(None)
    assert errs == []
    assert target.exists()


def test_w001_warns_when_oserror_raised(tmp_path, settings):
    """Covers the OSError except branch."""
    settings.LOGS_DIR = tmp_path
    from apps.observability import checks as obs_checks

    with patch.object(obs_checks.os, "access", side_effect=OSError("boom")):
        errs = obs_checks.check_logs_dir_writable(None)
    assert any(e.id == "observability.W001" for e in errs)


# --- H001/H002/H003 heartbeat-freshness checks ---------------------------------


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


def test_h001_fires_when_heartbeat_is_stale(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    # check_health.hourly has max_age=75 minutes; write one 2h old
    _write_hb(tmp_path, "check_health.hourly", age=timedelta(hours=2))
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H001" and "check_health.hourly" in e.msg for e in errs)


def test_h002_fires_when_heartbeat_never_seen(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""  # ensure non-agent mode so non-agent_only specs are checked
    (tmp_path / "heartbeats.jsonl").write_text("")
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H002" for e in errs)


def test_h003_fires_when_last_status_is_fail(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    _write_hb(tmp_path, "check_health.hourly", status="fail", age=timedelta(seconds=10))
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H003" and "check_health.hourly" in e.msg for e in errs)


def test_agent_only_specs_skipped_in_hub_mode(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""  # this host IS the hub, not an agent
    settings.CLUSTER_ENABLED = True
    (tmp_path / "heartbeats.jsonl").write_text("")
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    msgs = "\n".join(e.msg for e in errs)
    assert "push_to_hub" not in msgs
    assert "cluster_push.events" not in msgs


# --- Coverage extras -----------------------------------------------------------


def test_h_check_quiet_when_all_heartbeats_fresh_and_ok(tmp_path, settings):
    """Covers the path where a registered heartbeat is fresh + ok (no warnings)."""
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = ""  # non-agent mode; only non-agent_only specs are evaluated
    # Write one fresh OK heartbeat per non-agent_only spec so none produce warnings.
    from apps.observability.heartbeat_registry import HEARTBEAT_REGISTRY

    lines = []
    for name, spec in HEARTBEAT_REGISTRY.items():
        if spec.agent_only:
            continue
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
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
                    "status": "ok",
                }
            )
        )
    (tmp_path / "heartbeats.jsonl").write_text("\n".join(lines) + "\n")
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    # No warnings whatsoever in non-agent mode for these names.
    names_in_msgs = " ".join(e.msg for e in errs)
    assert "check_health.hourly" not in names_in_msgs
    assert "check_health.daily" not in names_in_msgs
    assert "preflight.scheduled" not in names_in_msgs


def test_h_check_handles_malformed_ts(tmp_path, settings):
    """Malformed ts → H001 fires (treat as maximally stale, not silently fresh)."""
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
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    assert any(e.id == "observability.H001" and "check_health.hourly" in e.msg for e in errs)


def test_h_check_evaluates_agent_only_specs_in_agent_mode(tmp_path, settings):
    """Covers the falsy branch of `if spec.agent_only and not agent_mode`."""
    settings.LOGS_DIR = tmp_path
    settings.HUB_URL = "https://hub.example.com"  # agent mode
    (tmp_path / "heartbeats.jsonl").write_text("")
    from apps.observability.checks import check_heartbeats_fresh

    errs = check_heartbeats_fresh(None)
    msgs = "\n".join(e.msg for e in errs)
    # In agent mode, agent_only specs ARE evaluated → H002 for them.
    assert "push_to_hub" in msgs
    assert "cluster_push.events" in msgs


def test_h001_message_uses_compact_duration_format():
    """H001 message uses _fmt_td (e.g. '2h00m' instead of '2:00:00.000000')."""
    from datetime import timedelta

    from apps.observability.checks import _fmt_td

    assert _fmt_td(timedelta(hours=2, minutes=14, seconds=37, microseconds=182943)) == "2h14m"
    assert _fmt_td(timedelta(minutes=7, seconds=23)) == "7m23s"
    assert _fmt_td(timedelta(seconds=45)) == "45s"
    assert _fmt_td(timedelta(seconds=0)) == "0s"
    assert _fmt_td(timedelta(seconds=-5)) == "0s"  # negative clamped
