"""Tests for observability models."""

import pytest


@pytest.mark.django_db
def test_create_minimal_destination():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="central-hub")
    dest = ClusterDestination.objects.create(
        name="central",
        hub_url="https://central.example.com",
        api_key=key,
    )
    assert dest.streams == "events,heartbeats"  # default
    assert dest.forward_received is False  # default
    assert dest.is_active is True  # default
    assert dest.max_batch_bytes == 10 * 1024 * 1024  # default
    assert dest.last_push_at is None
    assert dest.last_push_status == ""


@pytest.mark.django_db
def test_destination_name_is_unique():
    from django.db import IntegrityError

    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub")
    ClusterDestination.objects.create(name="dup", hub_url="https://a.example", api_key=key)
    with pytest.raises(IntegrityError):
        ClusterDestination.objects.create(name="dup", hub_url="https://b.example", api_key=key)


@pytest.mark.django_db
def test_destination_str_is_name():
    from apps.observability.models import ClusterDestination
    from config.models import APIKey

    key = APIKey.objects.create(name="hub")
    dest = ClusterDestination.objects.create(
        name="central", hub_url="https://e.example", api_key=key
    )
    assert str(dest) == "central"
