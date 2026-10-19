from datetime import timedelta

import pytest
from django.utils import timezone

from apps.console.queries.keys import build_key_editor, build_key_list
from config.api_keys import KEY_FIELDS, key_digest
from config.describing import Action
from config.models import APIKey

pytestmark = pytest.mark.django_db


def _key(name, **kw):
    return APIKey.objects.create(name=name, **kw)


def test_rows_are_ordered_by_name():
    _key("zeta")
    _key("alpha")

    assert [row.name for row in build_key_list().rows] == ["alpha", "zeta"]


def test_a_row_names_its_state_prefix_endpoints_and_use():
    used = timezone.now() - timedelta(hours=2)
    on = _key("hub", allowed_endpoints=["/alerts/webhook/"], last_used_at=used)
    off = _key("old", is_active=False)

    rows = {row.name: row for row in build_key_list().rows}

    assert (rows["hub"].active, rows["hub"].tone) == (True, "ok")
    assert rows["hub"].prefix == on.prefix
    assert rows["hub"].endpoints == "/alerts/webhook/"
    assert rows["hub"].last_used == used
    assert rows["hub"].edit == Action("hub", "view_key", {"pk": on.pk})
    assert rows["hub"].anchor == f"key-{on.pk}"
    assert (rows["old"].active, rows["old"].tone, rows["old"].pk) == (False, "muted", off.pk)
    assert rows["old"].endpoints == "All endpoints"
    assert rows["old"].last_used is None


def test_the_list_carries_the_add_action():
    keys = build_key_list(can_add=True)

    assert keys.can_add is True
    assert keys.add == Action("Add a key", "add_key", {})
    assert build_key_list().can_add is False


class TestTheEditor:
    def test_a_missing_key_is_none(self):
        assert build_key_editor(999, can_change=True) is None

    def test_an_existing_key(self):
        key = _key("hub", allowed_endpoints=["/a/"])

        editor = build_key_editor(key.pk, can_change=False)

        row = APIKey.objects.filter(pk=key.pk).values(*KEY_FIELDS)[0]
        assert editor.digest == key_digest(row)
        assert editor.url == f"/console/keys/{key.pk}/"
        assert editor.back_url == f"/console/keys/#key-{key.pk}"
        assert all(field.disabled for field in editor.form.fields.values())
        assert [(f.label, f.value) for f in editor.facts][0] == ("Prefix", f"{key.prefix}…")
        assert [f.label for f in editor.facts] == ["Prefix", "Created", "Last used"]
        assert editor.facts[2].value == "Never"

    def test_a_used_key_gives_the_time(self):
        key = _key("hub", last_used_at=timezone.now())

        assert build_key_editor(key.pk, can_change=True).facts[2].value != "Never"

    def test_a_carried_digest_is_kept(self):
        key = _key("hub")

        assert build_key_editor(key.pk, can_change=True, digest="old").digest == "old"

    def test_a_new_key(self):
        editor = build_key_editor(None, can_change=True)

        assert (editor.pk, editor.name, editor.digest, editor.facts) == (None, "", "", ())
        assert editor.url == "/console/keys/new/"
        assert editor.back_url == "/console/keys/"
        assert not editor.form.fields["name"].disabled
