"""Integration tests: existing commands emit heartbeats.

These tests rely on the autouse ``redirect_heartbeat_handler`` fixture
in ``conftest.py`` to redirect heartbeat writes into ``tmp_path``.
"""

import json
from pathlib import Path

import pytest


def _read(tmp_path: Path, name: str) -> list[dict]:
    p = tmp_path / name
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_check_health_emits_heartbeat(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command

    call_command("check_health")
    recs = _read(tmp_path, "heartbeats.jsonl")
    names = {r["name"] for r in recs}
    assert any(n.startswith("check_health") for n in names)


@pytest.mark.django_db
def test_preflight_emits_heartbeat(tmp_path, settings):
    settings.LOGS_DIR = tmp_path
    from django.core.management import call_command

    call_command("preflight", "--json")
    recs = _read(tmp_path, "heartbeats.jsonl")
    assert any(r["name"] == "preflight.scheduled" for r in recs)
