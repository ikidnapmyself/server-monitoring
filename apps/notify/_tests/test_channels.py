"""Saving a channel from the console, and what deleting one would cut."""

import hashlib
import json
from unittest.mock import patch

import pytest

from apps.notify.channels import (
    StaleChannel,
    channel_digest,
    lanes_cut_by_delete,
    save_channel,
)
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes

pytestmark = pytest.mark.django_db

FIELDS = ("name", "driver", "is_active", "description", "config")


def row(channel):
    return NotificationChannel.objects.filter(pk=channel.pk).values(*FIELDS).get()


@pytest.fixture
def channel():
    return NotificationChannel.objects.create(
        name="ops", driver="generic", config={"timeout": 5}, description="old"
    )


def test_a_fresh_save_writes(channel):
    before = channel.updated_at
    values = dict(row(channel), description="new", config={"timeout": 9})

    save_channel(channel.pk, values, expected=channel_digest(row(channel)))

    channel.refresh_from_db()
    assert channel.description == "new"
    assert channel.config == {"timeout": 9}
    assert channel.updated_at > before


def test_a_stale_digest_raises(channel):
    expected = channel_digest(row(channel))
    NotificationChannel.objects.filter(pk=channel.pk).update(description="someone else")

    with pytest.raises(StaleChannel):
        save_channel(channel.pk, {"description": "mine"}, expected=expected)

    channel.refresh_from_db()
    assert channel.description == "someone else"


def test_a_missing_channel_raises(channel):
    expected = channel_digest(row(channel))
    channel.delete()

    with pytest.raises(StaleChannel):
        save_channel(channel.pk, {"description": "mine"}, expected=expected)


def test_a_save_that_loses_the_race_after_its_read_writes_nothing(channel):
    old = row(channel)
    NotificationChannel.objects.filter(pk=channel.pk).update(config={"timeout": 60})

    with patch("apps.notify.channels._stored", return_value=[old]):
        with pytest.raises(StaleChannel):
            save_channel(channel.pk, {"config": {}}, expected=channel_digest(old))

    channel.refresh_from_db()
    assert channel.config == {"timeout": 60}


def test_the_digest_covers_the_five_fields_and_ignores_key_order(channel):
    stored = row(channel)
    assert channel_digest(stored) == channel_digest(dict(reversed(list(stored.items()))))
    assert channel_digest(stored) == channel_digest(dict(stored, updated_at="later"))
    for field, other in [
        ("name", "x"),
        ("driver", "slack"),
        ("is_active", False),
        ("description", "x"),
        ("config", {"timeout": 6}),
    ]:
        assert channel_digest(dict(stored, **{field: other})) != channel_digest(stored)


def test_the_lanes_cut_by_a_delete_are_the_ones_that_notify(channel):
    clear_lanes()
    later = PipelineDefinition.objects.create(
        name="later", priority=200, stages=["notify"], channel=channel
    )
    first = PipelineDefinition.objects.create(
        name="first", priority=10, stages=["check", "notify"], channel=channel
    )
    PipelineDefinition.objects.create(
        name="no-notify", priority=5, stages=["check"], channel=channel
    )
    other = NotificationChannel.objects.create(name="other", driver="generic")
    PipelineDefinition.objects.create(name="elsewhere", stages=["notify"], channel=other)

    assert lanes_cut_by_delete(channel) == [first, later]


def test_junk_stages_do_not_count_as_notify(channel):
    clear_lanes()
    PipelineDefinition.objects.create(name="junk", stages="notify", channel=channel)

    assert lanes_cut_by_delete(channel) == []


def test_the_digest_is_keyed_so_the_page_cannot_be_brute_forced(channel):
    stored = row(channel)
    plain = hashlib.sha256(json.dumps(stored, sort_keys=True, default=str).encode()).hexdigest()

    assert channel_digest(stored) != plain
    assert channel_digest(stored) == channel_digest(dict(stored))


def test_the_digest_is_a_sha256_hmac(channel):
    assert len(channel_digest(row(channel))) == 64
