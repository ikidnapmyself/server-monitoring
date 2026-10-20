"""The console's API key form, and the save that refuses a changed key."""

import pytest
from django.utils import timezone

from config.api_keys import (
    KEY_FIELDS,
    PATH_ERROR,
    APIKeyForm,
    StaleKey,
    endpoints_words,
    key_digest,
    save_key,
)
from config.models import APIKey
from config.stale import Stale

pytestmark = pytest.mark.django_db


def row(key):
    return APIKey.objects.filter(pk=key.pk).values(*KEY_FIELDS).get()


@pytest.fixture
def key():
    return APIKey.objects.create(name="ops", allowed_endpoints=["/alerts/webhook/", "/api/"])


def post(**overrides):
    data = {"name": "ops", "is_active": "on", "allowed_endpoints": "/alerts/webhook/\n/api/"}
    data.update(overrides)
    return data


def test_the_form_shows_the_stored_paths_one_per_line(key):
    form = APIKeyForm(instance=key)

    assert form["allowed_endpoints"].value() == "/alerts/webhook/\n/api/"
    assert 'rows="4"' in str(form["allowed_endpoints"])
    assert form.fields["name"].label == "Name"
    assert form.fields["is_active"].label == "On"
    assert form.fields["is_active"].help_text == "A key that's off is refused."
    assert form.fields["allowed_endpoints"].help_text == (
        "One path per line, such as /alerts/webhook/. Leave empty to allow every endpoint."
    )


def test_a_new_form_shows_no_paths():
    assert APIKeyForm()["allowed_endpoints"].value() == ""


def test_a_round_trip_keeps_the_stored_row(key):
    before = row(key)
    form = APIKeyForm(post(allowed_endpoints="  /alerts/webhook/ \n\n/api/\n"), instance=key)

    assert form.is_valid(), form.errors
    save_key(key.pk, form.cleaned_data, expected=key_digest(before))

    assert row(key) == before


def test_the_form_edits_the_row(key):
    form = APIKeyForm(post(name="renamed", allowed_endpoints=""), instance=key)
    assert form.is_valid(), form.errors

    save_key(key.pk, form.cleaned_data, expected=key_digest(row(key)))

    key.refresh_from_db()
    assert (key.name, key.is_active, key.allowed_endpoints) == ("renamed", True, [])


def test_unchecking_turns_the_key_off(key):
    form = APIKeyForm({"name": "ops", "allowed_endpoints": ""}, instance=key)

    assert form.is_valid(), form.errors
    assert form.cleaned_data["is_active"] is False


@pytest.mark.parametrize("text", ["alerts/webhook/", "/api/\nhttps://evil.example.com/"])
def test_each_line_must_be_a_path(text):
    form = APIKeyForm(post(allowed_endpoints=text))

    assert not form.is_valid()
    assert form.errors["allowed_endpoints"] == [PATH_ERROR]
    assert PATH_ERROR == "Each line must be a path starting with /."


@pytest.mark.parametrize("name", ["", "   "])
def test_a_blank_name_is_refused(name):
    form = APIKeyForm(post(name=name))

    assert not form.is_valid()
    assert "name" in form.errors


def test_a_name_another_key_has_is_allowed(key):
    form = APIKeyForm(post())

    assert form.is_valid(), form.errors


def test_a_stale_save_raises(key):
    expected = key_digest(row(key))
    APIKey.objects.filter(pk=key.pk).update(name="someone else")

    with pytest.raises(StaleKey) as raised:
        save_key(key.pk, {"name": "mine"}, expected=expected)

    assert isinstance(raised.value, Stale)
    key.refresh_from_db()
    assert key.name == "someone else"


def test_a_use_of_the_key_between_render_and_save_is_not_stale(key):
    expected = key_digest(row(key))
    APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())

    save_key(key.pk, {"name": "mine"}, expected=expected)

    key.refresh_from_db()
    assert key.name == "mine"
    assert key.last_used_at is not None


def test_the_digest_covers_only_the_edited_fields(key):
    stored = APIKey.objects.filter(pk=key.pk).values(*KEY_FIELDS, "key", "prefix").get()
    digest = key_digest(stored)

    assert digest == key_digest(dict(stored, key="x", prefix="y"))
    for field, other in [("name", "x"), ("is_active", False), ("allowed_endpoints", [])]:
        assert key_digest(dict(stored, **{field: other})) != digest


def test_a_save_never_changes_the_token(key):
    hashed, prefix = key.key, key.prefix
    values = {"name": "x", "is_active": False, "allowed_endpoints": [], "key": "", "prefix": ""}

    save_key(key.pk, values, expected=key_digest(row(key)))

    key.refresh_from_db()
    assert (key.key, key.prefix) == (hashed, prefix)
    assert key.name == "x"


def test_endpoints_words():
    assert endpoints_words(APIKey(allowed_endpoints=[])) == "All endpoints"
    assert endpoints_words(APIKey(allowed_endpoints=["/a/", "/b/"])) == "/a/ and /b/"
    paths = ["/a/", "/b/", "/c/", "/d/", "/e/"]
    assert endpoints_words(APIKey(allowed_endpoints=paths)) == "/a/, /b/, /c/ and 2 more"
