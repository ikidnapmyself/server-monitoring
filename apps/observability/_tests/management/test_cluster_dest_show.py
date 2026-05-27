"""Tests for `manage.py cluster_dest_show <name>`."""

import json
from datetime import datetime, timezone

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_show_existing_text(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
        streams="events",
        forward_received=True,
        last_push_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        last_push_status="ok",
    )
    call_command("cluster_dest_show", "central")
    out = capsys.readouterr().out
    assert "central" in out
    assert "https://central.example.com" in out
    assert "events" in out
    assert "ok" in out
    assert "hub-key" in out
    assert "No pushes yet" in out


@pytest.mark.django_db
def test_show_existing_json(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
    )
    call_command("cluster_dest_show", "central", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "central"
    assert payload["hub_url"] == "https://central.example.com"
    assert payload["streams"] == "events,heartbeats"
    assert payload["forward_received"] is False
    assert payload["is_active"] is True
    assert payload["last_push_at"] is None
    assert payload["last_push_status"] is None
    assert payload["api_key"] == "hub-key"
    assert payload["recent_pushes"] == []


@pytest.mark.django_db
def test_show_unknown_raises():
    with pytest.raises(CommandError, match="No destination named 'ghost'"):
        call_command("cluster_dest_show", "ghost")


@pytest.mark.django_db
def test_show_text_without_last_push(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(name="fresh", hub_url="https://f.example.com", api_key=key)
    call_command("cluster_dest_show", "fresh")
    out = capsys.readouterr().out
    assert "fresh" in out
    assert "—" in out
