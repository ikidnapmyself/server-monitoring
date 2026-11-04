"""Tests for the audit entry recorder and its diff."""

import datetime
import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from apps.console.audit import config_keys, config_state, diff, record, snapshot, unlisted
from apps.console.models import AuditEntry


def _request(user=None, peer="192.0.2.1", forwarded=None):
    meta = {"REMOTE_ADDR": peer}
    if forwarded is not None:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    request = RequestFactory().post("/", **meta)
    request.user = user or AnonymousUser()
    return request


@pytest.fixture
def root(db):
    return get_user_model().objects.create_superuser("root", password="x")


def test_equal_dicts_have_no_diff():
    assert diff({"a": 1, "b": "x"}, {"a": 1, "b": "x"}) == {}


def test_a_changed_value_records_old_and_new():
    assert diff({"a": 1, "b": "x"}, {"a": 2, "b": "x"}) == {"a": {"old": 1, "new": 2}}


def test_added_and_removed_keys_count_as_changed():
    assert diff({"gone": 1}, {"new": 2}) == {
        "gone": {"old": 1, "new": None},
        "new": {"old": None, "new": 2},
    }


def test_a_secret_records_only_that_it_changed():
    changes = diff({"key": "old-secret"}, {"key": "new-secret"}, secret=frozenset({"key"}))
    assert changes == {"key": {"changed": True}}
    text = json.dumps(changes)
    assert "old-secret" not in text
    assert "new-secret" not in text


def test_an_unchanged_secret_is_left_out():
    assert diff({"key": "s"}, {"key": "s"}, secret=frozenset({"key"})) == {}


def test_snapshot_makes_values_json_safe(root):
    root.last_login = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=datetime.timezone.utc)
    snap = snapshot(root, ["username", "is_active", "last_login", "email"])
    assert snap == {
        "username": "root",
        "is_active": True,
        "last_login": "2026-11-03T12:00:00+00:00",
        "email": "",
    }
    json.dumps(snap)


def test_snapshot_strings_what_json_refuses():
    class Thing:
        price = Decimal("1.50")
        tags = ["a"]
        nothing = None
        ratio = float("nan")
        day = datetime.date(2026, 11, 3)

    snap = snapshot(Thing(), ["price", "tags", "nothing", "ratio", "day"])
    assert snap == {
        "price": "1.50",
        "tags": ["a"],
        "nothing": None,
        "ratio": "nan",
        "day": "2026-11-03",
    }
    json.dumps(snap, allow_nan=False)


def test_record_with_an_instance(root):
    entry = record(_request(root), "user.edit", root, changes={"email": {"old": "", "new": "a"}})
    entry.refresh_from_db()
    assert entry.actor == root
    assert entry.actor_name == "root"
    assert entry.action == "user.edit"
    assert (entry.target_kind, entry.target_id, entry.target_name) == ("user", root.pk, "root")
    assert entry.changes == {"email": {"old": "", "new": "a"}}
    assert entry.address == "192.0.2.1"
    assert entry.at is not None


def test_record_with_a_triple(root):
    entry = record(_request(root), "inbox.drain", ("inbox", None, "inbox"))
    assert (entry.target_kind, entry.target_id, entry.target_name) == ("inbox", None, "inbox")
    assert entry.changes == {}


def test_record_truncates_a_long_target_name(root):
    entry = record(_request(root), "x.y", ("thing", 1, "n" * 300))
    entry.refresh_from_db()
    assert entry.target_name == "n" * 255


def test_record_with_no_target(root):
    entry = record(_request(root), "inbox.drain")
    assert (entry.target_kind, entry.target_id, entry.target_name) == ("", None, "")


def test_record_takes_an_explicit_actor_over_the_request(root):
    entry = record(_request(), "session.sign_in", root, actor=root)
    assert (entry.actor, entry.actor_name) == (root, "root")


def test_record_signed_out_has_no_actor(db):
    entry = record(_request(), "session.sign_in_failed", ("user", None, "mallory"))
    assert entry.actor is None
    assert entry.actor_name == ""


@override_settings(TRUSTED_PROXIES=("10.0.0.0/8",))
def test_record_takes_the_client_address_behind_a_trusted_proxy(root):
    entry = record(_request(root, peer="10.0.0.5", forwarded="198.51.100.7"), "x.y")
    assert entry.address == "198.51.100.7"


def test_an_entry_outlives_its_actor(root):
    entry = record(_request(root), "user.edit", root)
    root.delete()
    entry.refresh_from_db()
    assert entry.actor is None
    assert entry.actor_name == "root"


def test_entries_are_newest_first(root):
    first = record(_request(root), "a.b")
    second = record(_request(root), "a.b")
    assert list(AuditEntry.objects.all()) == [second, first]


class _Spec:
    def __init__(self, key, secret=False):
        self.key, self.secret = key, secret


class _Driver:
    credential_aliases = {"url": "endpoint"}

    @classmethod
    def all_config_fields(cls):
        return (_Spec("endpoint", secret=True), _Spec("timeout"))


class _Provider:
    @classmethod
    def all_config_fields(cls):
        return (_Spec("model"), _Spec("timeout", secret=True))


def test_config_state_flattens_each_key():
    when = datetime.date(2026, 11, 3)
    assert config_state({"url": "u", "on": when}) == {"config.url": "u", "config.on": "2026-11-03"}


def test_a_config_that_isnt_a_dict_has_no_state():
    assert config_state(["junk"]) == {}


def test_config_keys_splits_secret_from_public():
    assert config_keys([_Driver], secret=True) == {"config.endpoint", "config.url"}
    assert config_keys([_Driver], secret=False) == {"config.timeout"}


def test_a_key_secret_for_any_class_is_never_public():
    assert config_keys([_Driver, _Provider], secret=False) == {"config.model"}
    assert config_keys([_Driver, _Provider], secret=True) == {
        "config.endpoint",
        "config.url",
        "config.timeout",
    }


def test_unlisted_names_config_keys_nobody_declared_public():
    public = frozenset({"config.timeout"})
    before = {"name": "a", "config.timeout": 1, "config.legacy": "x"}
    after = {"name": "b", "config.timeout": 2, "config.other": "y"}
    assert unlisted(before, after, public) == {"config.legacy", "config.other"}
