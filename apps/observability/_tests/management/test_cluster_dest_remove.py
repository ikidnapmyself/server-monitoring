"""Tests for `manage.py cluster_dest_remove`."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_soft_remove_sets_inactive(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key
    )
    call_command("cluster_dest_remove", "--name", "central")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.is_active is False
    out = capsys.readouterr().out
    assert "deactivated" in out or "Deactivated" in out
    assert "central" in out


@pytest.mark.django_db
def test_hard_remove_deletes_row(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central", hub_url="https://central.example.com", api_key=key
    )
    call_command("cluster_dest_remove", "--name", "central", "--hard")
    assert not ClusterDestination.objects.filter(name="central").exists()
    out = capsys.readouterr().out
    assert "deleted" in out.lower()
    assert "central" in out


@pytest.mark.django_db
def test_remove_unknown_raises():
    with pytest.raises(CommandError, match="No destination named 'ghost'"):
        call_command("cluster_dest_remove", "--name", "ghost")


@pytest.mark.django_db
def test_soft_remove_is_idempotent(capsys):
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub-key")
    ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
        is_active=False,
    )
    call_command("cluster_dest_remove", "--name", "central")
    dest = ClusterDestination.objects.get(name="central")
    assert dest.is_active is False
    out = capsys.readouterr().out
    assert "central" in out
