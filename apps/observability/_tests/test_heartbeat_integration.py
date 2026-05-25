"""Integration tests: existing commands emit heartbeats.

These tests rely on the autouse ``redirect_heartbeat_handler`` fixture
in ``conftest.py`` to redirect heartbeat writes into ``tmp_path``.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.checkers.checkers.base import CheckResult, CheckStatus


def _read(tmp_path: Path, name: str) -> list[dict]:
    p = tmp_path / name
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_check_health_emits_heartbeat(tmp_path, settings):
    # Registry mocked: real checkers make this host-flaky (CRITICAL → sys.exit(2)).
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command

    mock_checker = MagicMock()
    mock_checker.return_value.run.return_value = CheckResult(
        status=CheckStatus.OK,
        message="ok",
        metrics={},
        checker_name="cpu",
    )
    with patch.dict(
        "apps.checkers.management.commands.check_health.CHECKER_REGISTRY",
        {"cpu": mock_checker},
        clear=True,
    ):
        call_command("check_health")

    recs = _read(tmp_path, "heartbeats.jsonl")
    names = {r["name"] for r in recs}
    assert any(n.startswith("check_health") for n in names)


def test_check_health_nonzero_exit_still_emits_ok_heartbeat(tmp_path, settings):
    """SystemExit from check_health must not leave the heartbeat at status=running.

    Regression test: `sys.exit(exit_code)` was previously inside the
    `with heartbeat(...)` block. Because `SystemExit` inherits from
    `BaseException` (not `Exception`), the context manager's `except Exception`
    clause would not intercept it — neither the `ok` nor the `fail` branch
    would run, and the heartbeat stream would stay stuck on `status=running`.

    The fix moves `sys.exit` outside the `with` block so the `ok` branch
    always emits a completion record on normal control flow, even when the
    command exits non-zero because a checker reported CRITICAL.
    """
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command

    mock_checker = MagicMock()
    mock_checker.return_value.run.return_value = CheckResult(
        status=CheckStatus.CRITICAL,
        message="Simulated critical",
        metrics={},
        checker_name="cpu",
    )
    with patch.dict(
        "apps.checkers.management.commands.check_health.CHECKER_REGISTRY",
        {"cpu": mock_checker},
        clear=True,
    ):
        with pytest.raises(SystemExit) as exc_info:
            call_command("check_health", "cpu")

    # Confirm the command did exit non-zero (default critical → exit 2).
    assert exc_info.value.code == 2

    recs = _read(tmp_path, "heartbeats.jsonl")
    statuses = [r["status"] for r in recs if r.get("name", "").startswith("check_health")]
    # The crucial assertion: SOMETHING terminal was emitted. Without the fix,
    # we'd see only `running` and the freshness check would later flag the
    # heartbeat as stale even though the job ran.
    assert any(
        s in ("ok", "fail") for s in statuses
    ), f"Expected ok/fail completion heartbeat but got only {statuses}"


@pytest.mark.django_db
def test_preflight_emits_heartbeat(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command

    call_command("preflight", "--json")
    recs = _read(tmp_path, "heartbeats.jsonl")
    assert any(r["name"] == "preflight.scheduled" for r in recs)
