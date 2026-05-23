"""Tests for `manage.py read_logs view`."""

import json

from django.core.management import call_command


def _write(tmp_path, records):
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")


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


def test_view_json_output(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="hello")])
    call_command("read_logs", "view", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(line) for line in out]
    assert parsed[0]["msg"] == "hello"


def test_view_filter_by_category(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(category="alerts"), _rec(category="notify")])
    call_command("read_logs", "view", "--category", "notify", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    assert all(json.loads(line)["category"] == "notify" for line in out)


def test_view_pretty_output_includes_trace(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(
        tmp_path,
        [_rec(msg="boot", trace_id="abcdef1234567890", logger="apps.alerts.svc")],
    )
    call_command("read_logs", "view", "--no-pager")
    out = capsys.readouterr().out
    assert "boot" in out
    assert "INFO" in out
    assert "apps.alerts.svc" in out
    # trace_id truncated to 8 chars
    assert "trace=abcdef12" in out


def test_view_pretty_output_no_trace(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="bare")])
    call_command("read_logs", "view", "--no-pager")
    out = capsys.readouterr().out
    assert "bare" in out
    assert "trace=" not in out


def test_view_plain_output(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="plain-msg")])
    call_command("read_logs", "view", "--plain", "--no-pager")
    out = capsys.readouterr().out
    assert "plain-msg" in out


def test_view_heartbeats_stream(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    (tmp_path / "heartbeats.jsonl").write_text(
        json.dumps(_rec(msg="heartbeat-msg", category="heartbeat")) + "\n"
    )
    call_command("read_logs", "view", "--stream", "heartbeats", "--json", "--no-pager")
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["msg"] == "heartbeat-msg"


def test_view_instance_directory(tmp_path, settings, capsys):
    settings.LOGS_DIR = tmp_path
    cluster_dir = tmp_path / "cluster" / "node-a"
    cluster_dir.mkdir(parents=True)
    (cluster_dir / "events.jsonl").write_text(json.dumps(_rec(msg="from-node-a")) + "\n")
    call_command(
        "read_logs",
        "view",
        "--instance",
        "node-a",
        "--json",
        "--no-pager",
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["msg"] == "from-node-a"


def test_unknown_action_raises(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from apps.observability.management.commands.read_logs import Command

    cmd = Command()
    try:
        cmd.handle(action="bogus")
    except NotImplementedError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("expected NotImplementedError")
