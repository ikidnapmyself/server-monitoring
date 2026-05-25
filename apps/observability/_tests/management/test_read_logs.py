"""Tests for `manage.py read_logs view`."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


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
    _write(tmp_path, [_rec(msg="plain-msg", logger="apps.alerts.svc")])
    call_command("read_logs", "view", "--plain", "--no-pager")
    out = capsys.readouterr().out.strip()
    assert out == "plain-msg"


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


def test_view_instance_path_traversal_rejected(tmp_path, settings):
    # `--instance "../etc"` resolves outside LOGS_DIR/cluster/ and must be
    # refused by the path-traversal guard rather than silently reading from
    # an attacker-chosen directory.
    settings.LOGS_DIR = tmp_path
    with pytest.raises(CommandError, match="--instance must be a simple name"):
        call_command(
            "read_logs",
            "view",
            "--instance",
            "../etc",
            "--json",
            "--no-pager",
        )


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


def test_view_streams_to_less_when_stdout_is_tty(tmp_path, settings):
    # Default behaviour (no --no-pager) on an interactive stdout: lines flow
    # into `less -FRX` via subprocess.Popen, written one at a time rather
    # than buffered into a single string.
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="line-1"), _rec(msg="line-2")])

    fake_pager = MagicMock()
    fake_pager.stdin = MagicMock()
    fake_pager.stdin.write = MagicMock()
    fake_pager.wait = MagicMock(return_value=0)

    with (
        patch(
            "apps.observability.management.commands.read_logs.sys.stdout.isatty",
            return_value=True,
        ),
        patch(
            "apps.observability.management.commands.read_logs.shutil.which",
            return_value="/usr/bin/less",
        ),
        patch(
            "apps.observability.management.commands.read_logs.subprocess.Popen",
            return_value=fake_pager,
        ) as mock_popen,
    ):
        call_command("read_logs", "view")

    mock_popen.assert_called_once()
    argv = mock_popen.call_args.args[0]
    assert argv[0] == "/usr/bin/less"
    assert "-FRX" in argv
    # Lines were streamed (one write per line, not one bulk write).
    writes = [c.args[0] for c in fake_pager.stdin.write.call_args_list]
    assert any("line-1" in w for w in writes)
    assert any("line-2" in w for w in writes)
    fake_pager.wait.assert_called_once()


def test_view_skips_pager_when_stdout_not_tty(tmp_path, settings, capsys):
    # Non-interactive stdout (CI, redirected output): pager is skipped
    # without needing the --no-pager flag.
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="non-tty")])

    with patch("apps.observability.management.commands.read_logs.subprocess.Popen") as mock_popen:
        call_command("read_logs", "view")

    mock_popen.assert_not_called()
    assert "non-tty" in capsys.readouterr().out


def test_view_skips_pager_when_less_unavailable(tmp_path, settings, capsys):
    # Interactive stdout but `less` not on PATH: fall back to direct stdout.
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="no-less")])

    with (
        patch(
            "apps.observability.management.commands.read_logs.sys.stdout.isatty",
            return_value=True,
        ),
        patch(
            "apps.observability.management.commands.read_logs.shutil.which",
            return_value=None,
        ),
        patch("apps.observability.management.commands.read_logs.subprocess.Popen") as mock_popen,
    ):
        call_command("read_logs", "view")

    mock_popen.assert_not_called()
    assert "no-less" in capsys.readouterr().out


def test_view_broken_pipe_is_swallowed(tmp_path, settings):
    # If the user quits `less` early, stdin writes raise BrokenPipeError.
    # The command must handle it cleanly rather than propagate.
    settings.LOGS_DIR = tmp_path
    _write(tmp_path, [_rec(msg="a"), _rec(msg="b")])

    fake_pager = MagicMock()
    fake_pager.stdin = MagicMock()
    fake_pager.stdin.write = MagicMock(side_effect=BrokenPipeError)
    fake_pager.stdin.close = MagicMock(side_effect=BrokenPipeError)
    fake_pager.wait = MagicMock(return_value=0)

    with (
        patch(
            "apps.observability.management.commands.read_logs.sys.stdout.isatty",
            return_value=True,
        ),
        patch(
            "apps.observability.management.commands.read_logs.shutil.which",
            return_value="/usr/bin/less",
        ),
        patch(
            "apps.observability.management.commands.read_logs.subprocess.Popen",
            return_value=fake_pager,
        ),
    ):
        # Must not raise.
        call_command("read_logs", "view")
    fake_pager.wait.assert_called_once()
