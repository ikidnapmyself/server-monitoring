"""Saving a channel from the console, and what deleting one would cut."""

import json

import pytest
from django.utils.crypto import salted_hmac

from apps.notify.channels import (
    StaleChannel,
    channel_digest,
    lanes_cut_by_delete,
    save_channel,
)
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.testing import clear_lanes
from config.stale import Stale

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
    values = dict(row(channel), description="new")

    save_channel(channel.pk, values, expected=channel_digest(row(channel)))

    channel.refresh_from_db()
    assert channel.description == "new"


def test_a_stale_save_raises_stale_channel(channel):
    expected = channel_digest(row(channel))
    NotificationChannel.objects.filter(pk=channel.pk).update(description="someone else")

    with pytest.raises(StaleChannel):
        save_channel(channel.pk, {"description": "mine"}, expected=expected)
    assert issubclass(StaleChannel, Stale)


def test_the_digest_is_unchanged_by_the_move_to_config(channel):
    stored = row(channel)
    payload = json.dumps(stored, sort_keys=True, default=str)

    assert channel_digest(stored) == (
        salted_hmac("notify.channel", payload, algorithm="sha256").hexdigest()
    )


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
