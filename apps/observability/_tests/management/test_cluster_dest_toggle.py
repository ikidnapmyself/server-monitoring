"""Tests for `manage.py cluster_dest_toggle`."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_toggle_true_to_false(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key, is_active=True
    )
    call_command("cluster_dest_toggle", "--name", "central")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.is_active is False
    out = capsys.readouterr().out
    assert "central" in out
    assert "False" in out or "inactive" in out.lower()


@pytest.mark.django_db
def test_toggle_round_trip(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key, is_active=True
    )
    call_command("cluster_dest_toggle", "--name", "central")
    call_command("cluster_dest_toggle", "--name", "central")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.is_active is True


@pytest.mark.django_db
def test_toggle_false_to_true(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
        is_active=False,
    )
    call_command("cluster_dest_toggle", "--name", "central")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.is_active is True


@pytest.mark.django_db
def test_toggle_unknown_raises():
    with pytest.raises(CommandError, match="No destination named 'ghost'"):
        call_command("cluster_dest_toggle", "--name", "ghost")
