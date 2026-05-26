"""Tests for `manage.py cluster_dest_forward`."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_forward_on_sets_true(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key
    )
    call_command("cluster_dest_forward", "--name", "central", "on")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.forward_received is True
    out = capsys.readouterr().out
    assert "central" in out
    assert "True" in out


@pytest.mark.django_db
def test_forward_off_sets_false(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
        forward_received=True,
    )
    call_command("cluster_dest_forward", "--name", "central", "off")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.forward_received is False
    out = capsys.readouterr().out
    assert "central" in out
    assert "False" in out


@pytest.mark.django_db
def test_forward_invalid_state_raises():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key
    )
    with pytest.raises(CommandError, match="forward state must be 'on' or 'off'"):
        call_command("cluster_dest_forward", "--name", "central", "maybe")


@pytest.mark.django_db
def test_forward_unknown_name_raises():
    with pytest.raises(CommandError, match="No destination named 'ghost'"):
        call_command("cluster_dest_forward", "--name", "ghost", "on")
