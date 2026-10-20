"""The row digest an editor carries, and the save that refuses a changed row."""

import hashlib
import json
from unittest.mock import patch

import pytest
from django.utils.crypto import salted_hmac

from apps.notify.models import NotificationChannel
from config.stale import Stale, row_digest, save_if_unchanged

pytestmark = pytest.mark.django_db

FIELDS = ("name", "driver", "is_active", "description", "config")
SALT = "test.row"


def row(channel):
    return NotificationChannel.objects.filter(pk=channel.pk).values(*FIELDS).get()


def digest(stored):
    return row_digest(SALT, stored, FIELDS)


def save(pk, values, expected):
    save_if_unchanged(NotificationChannel, pk, FIELDS, values, salt=SALT, expected=expected)


@pytest.fixture
def channel():
    return NotificationChannel.objects.create(
        name="ops", driver="generic", config={"timeout": 5}, description="old"
    )


def test_a_fresh_save_writes(channel):
    before = channel.updated_at
    values = dict(row(channel), description="new", config={"timeout": 9})

    save(channel.pk, values, digest(row(channel)))

    channel.refresh_from_db()
    assert channel.description == "new"
    assert channel.config == {"timeout": 9}
    assert channel.updated_at > before


def test_a_stale_digest_raises(channel):
    expected = digest(row(channel))
    NotificationChannel.objects.filter(pk=channel.pk).update(description="someone else")

    with pytest.raises(Stale):
        save(channel.pk, {"description": "mine"}, expected)

    channel.refresh_from_db()
    assert channel.description == "someone else"


def test_a_missing_row_raises(channel):
    expected = digest(row(channel))
    channel.delete()

    with pytest.raises(Stale):
        save(channel.pk, {"description": "mine"}, expected)


def test_a_save_that_loses_the_race_after_its_read_writes_nothing(channel):
    old = row(channel)
    NotificationChannel.objects.filter(pk=channel.pk).update(config={"timeout": 60})

    with patch("config.stale._stored", return_value=[old]):
        with pytest.raises(Stale):
            save(channel.pk, {"config": {}}, digest(old))

    channel.refresh_from_db()
    assert channel.config == {"timeout": 60}


def test_the_digest_covers_the_fields_and_ignores_key_order(channel):
    stored = row(channel)
    assert digest(stored) == digest(dict(reversed(list(stored.items()))))
    assert digest(stored) == digest(dict(stored, updated_at="later"))
    for field, other in [
        ("name", "x"),
        ("driver", "slack"),
        ("is_active", False),
        ("description", "x"),
        ("config", {"timeout": 6}),
    ]:
        assert digest(dict(stored, **{field: other})) != digest(stored)


def test_the_digest_depends_on_the_salt(channel):
    stored = row(channel)

    assert row_digest("other.row", stored, FIELDS) != digest(stored)


def test_the_digest_is_keyed_so_the_page_cannot_be_brute_forced(channel):
    stored = row(channel)
    plain = hashlib.sha256(json.dumps(stored, sort_keys=True, default=str).encode()).hexdigest()

    assert digest(stored) != plain
    assert digest(stored) == digest(dict(stored))


def test_the_digest_is_a_sha256_hmac_of_the_sorted_fields(channel):
    stored = row(channel)
    payload = json.dumps(stored, sort_keys=True, default=str)

    assert digest(stored) == salted_hmac(SALT, payload, algorithm="sha256").hexdigest()
    assert len(digest(stored)) == 64


def test_a_model_without_updated_at_saves_without_stamping_it():
    from config.models import APIKey

    key = APIKey.objects.create(name="ops")
    fields = ("name", "is_active")
    stored = APIKey.objects.filter(pk=key.pk).values(*fields).get()

    save_if_unchanged(
        APIKey,
        key.pk,
        fields,
        {"name": "renamed"},
        salt=SALT,
        expected=row_digest(SALT, stored, fields),
    )

    key.refresh_from_db()
    assert key.name == "renamed"
