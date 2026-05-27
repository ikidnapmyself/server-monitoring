"""Tests for `manage.py cluster_dest_list`."""

import json
from datetime import datetime, timezone

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_list_empty_text(capsys):
    call_command("cluster_dest_list")
    out = capsys.readouterr().out
    assert "No destinations" in out


@pytest.mark.django_db
def test_list_empty_json(capsys):
    call_command("cluster_dest_list", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


@pytest.mark.django_db
def test_list_text_sorted_by_name(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(name="zeta", hub_url="https://z.example.com", api_key=key)
    ClusterDestination.objects.create(name="alpha", hub_url="https://a.example.com", api_key=key)
    call_command("cluster_dest_list")
    out = capsys.readouterr().out
    assert out.index("alpha") < out.index("zeta")
    assert "https://a.example.com" in out
    assert "https://z.example.com" in out
    # Default values shown as em dash for un-pushed destinations.
    assert "—" in out


@pytest.mark.django_db
def test_list_json_structure(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="beta",
        hub_url="https://b.example.com",
        api_key=key,
        last_push_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        last_push_status="ok",
    )
    ClusterDestination.objects.create(name="alpha", hub_url="https://a.example.com", api_key=key)
    call_command("cluster_dest_list", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in payload] == ["alpha", "beta"]
    fields = {
        "name",
        "hub_url",
        "streams",
        "forward_received",
        "is_active",
        "last_push_at",
        "last_push_status",
    }
    assert fields.issubset(payload[0].keys())
    assert payload[0]["last_push_at"] is None
    assert payload[0]["last_push_status"] is None
    assert payload[1]["last_push_at"] is not None
    assert payload[1]["last_push_status"] == "ok"


@pytest.mark.django_db
def test_list_shows_last_push_info_in_text(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="filled",
        hub_url="https://f.example.com",
        api_key=key,
        last_push_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        last_push_status="ok",
    )
    call_command("cluster_dest_list")
    out = capsys.readouterr().out
    assert "ok" in out
    assert "2026-05-01" in out
