"""Tests for `manage.py cluster_dest_add`."""

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_add_creates_destination():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    APIKey.objects.create(name="hub-key")
    call_command(
        "cluster_dest_add",
        "--name",
        "central",
        "--url",
        "https://central.example.com",
        "--api-key",
        "hub-key",
    )
    dest = ClusterDestination.objects.get(name="central")
    assert dest.hub_url == "https://central.example.com"
    assert dest.streams == "events,heartbeats"
    assert dest.forward_received is False
    assert dest.is_active is True


@pytest.mark.django_db
def test_add_duplicate_name_raises():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(name="central", hub_url="https://a.example", api_key=key)
    with pytest.raises(CommandError, match="already exists"):
        call_command(
            "cluster_dest_add",
            "--name",
            "central",
            "--url",
            "https://b.example.com",
            "--api-key",
            "hub-key",
        )


@pytest.mark.django_db
def test_add_unknown_api_key_raises():
    with pytest.raises(CommandError, match="No APIKey named"):
        call_command(
            "cluster_dest_add",
            "--name",
            "central",
            "--url",
            "https://central.example.com",
            "--api-key",
            "ghost-key",
        )


@pytest.mark.django_db
def test_add_with_forward_flag():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    APIKey.objects.create(name="hub-key")
    call_command(
        "cluster_dest_add",
        "--name",
        "regional",
        "--url",
        "https://regional.example.com",
        "--api-key",
        "hub-key",
        "--forward",
    )
    dest = ClusterDestination.objects.get(name="regional")
    assert dest.forward_received is True


@pytest.mark.django_db
def test_add_custom_streams(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    APIKey.objects.create(name="hub-key")
    call_command(
        "cluster_dest_add",
        "--name",
        "events-only",
        "--url",
        "https://events.example.com",
        "--api-key",
        "hub-key",
        "--streams",
        "events",
    )
    dest = ClusterDestination.objects.get(name="events-only")
    assert dest.streams == "events"
    out = capsys.readouterr().out
    assert "Created destination 'events-only'" in out
    assert "https://events.example.com" in out


@pytest.mark.django_db
def test_add_json_output(capsys):
    from config.models import APIKey

    APIKey.objects.create(name="hub-key")
    call_command(
        "cluster_dest_add",
        "--name",
        "json-dest",
        "--url",
        "https://json.example.com",
        "--api-key",
        "hub-key",
        "--json",
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["name"] == "json-dest"
    assert payload["hub_url"] == "https://json.example.com"
    assert payload["streams"] == "events,heartbeats"
    assert payload["forward_received"] is False
    assert isinstance(payload["id"], int)
