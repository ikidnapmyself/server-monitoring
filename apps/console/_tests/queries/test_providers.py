import pytest

from apps.console.queries.providers import (
    FALLBACK,
    UnknownProvider,
    build_provider_deletion,
    build_provider_editor,
    build_provider_list,
    provider_choices,
)
from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.saving import FIELDS, provider_digest
from config.describing import Action

pytestmark = pytest.mark.django_db


def _provider(name, kind="claude", **kw):
    return IntelligenceProvider.objects.create(name=name, provider=kind, **kw)


def test_the_active_provider_comes_first_then_by_name():
    _provider("zeta")
    _provider("beta", kind="openai")
    _provider("omega", is_active=True)

    assert [row.name for row in build_provider_list().rows] == ["omega", "beta", "zeta"]


def test_a_row_names_its_kind_state_description_and_link():
    on = _provider("main", is_active=True, description="The one we pay for")
    odd = _provider("odd", kind="carrier_pigeon")

    rows = {row.name: row for row in build_provider_list().rows}

    assert rows["main"].kind_label == "Claude (Anthropic)"
    assert (rows["main"].active, rows["main"].tone) == (True, "ok")
    assert rows["main"].description == "The one we pay for"
    assert rows["main"].edit == Action("main", "view_provider", {"pk": on.pk})
    assert rows["main"].anchor == f"provider-{on.pk}"
    assert rows["odd"].kind_label == "A provider this hub doesn't have"
    assert (rows["odd"].active, rows["odd"].tone, rows["odd"].pk) == (False, "muted", odd.pk)


def test_the_headline_names_the_provider_in_use():
    _provider("main", is_active=True)

    assert build_provider_list().headline == "Analysis uses main."


def test_with_none_on_the_headline_names_the_fallback():
    _provider("spare")

    assert build_provider_list().headline == FALLBACK
    assert FALLBACK == "No provider is on, so analysis uses the built-in local analyzer."


def test_the_list_carries_the_add_action():
    providers = build_provider_list(can_add=True)

    assert providers.can_add is True
    assert providers.add == Action("Add a provider", "add_provider", {})
    assert build_provider_list().can_add is False


def test_the_choices_follow_the_model_choices():
    choices = provider_choices()

    assert [(c.key, c.label) for c in choices] == list(IntelligenceProvider.PROVIDER_CHOICES)
    assert choices[0].url == f"/console/providers/new/?provider={choices[0].key}"


class TestTheEditor:
    def test_a_missing_provider_is_none(self):
        assert build_provider_editor(999, can_change=True) is None

    def test_a_missing_provider_wins_over_a_bad_kind(self):
        assert build_provider_editor(999, kind="nope", can_change=True) is None

    def test_an_existing_provider(self):
        p = _provider("main", config={"api_key": "sk-x", "extra": 1})

        editor = build_provider_editor(p.pk, can_change=False)

        row = IntelligenceProvider.objects.filter(pk=p.pk).values(*FIELDS)[0]
        assert editor.digest == provider_digest(row)
        assert editor.url == f"/console/providers/{p.pk}/?provider=claude"
        assert editor.back_url == f"/console/providers/#provider-{p.pk}"
        assert editor.form.fields["name"].disabled
        labels = [fact.label for fact in editor.facts]
        assert labels == [
            "Provider",
            "Settings this provider doesn't declare",
            "Created",
            "Updated",
        ]
        assert editor.facts[0].value == "claude"
        assert editor.facts[1].value == "extra"

    def test_no_unknown_settings_leaves_that_fact_out(self):
        p = _provider("main")

        editor = build_provider_editor(p.pk, can_change=True)

        assert [fact.label for fact in editor.facts] == ["Provider", "Created", "Updated"]

    def test_a_carried_digest_is_kept(self):
        p = _provider("main")

        assert build_provider_editor(p.pk, can_change=True, digest="old").digest == "old"

    def test_a_new_provider(self):
        editor = build_provider_editor(None, kind="openai", can_change=True)

        assert (editor.pk, editor.name, editor.digest, editor.facts) == (None, "", "", ())
        assert editor.url == "/console/providers/new/?provider=openai"
        assert editor.back_url == "/console/providers/"
        assert editor.form.kind == "openai"

    @pytest.mark.parametrize("kind", ["", "nope", "local"])
    def test_a_new_provider_needs_a_kind_the_hub_offers(self, kind):
        with pytest.raises(UnknownProvider):
            build_provider_editor(None, kind=kind, can_change=True)

    def test_an_existing_provider_refuses_a_kind_it_isnt(self):
        p = _provider("main")

        with pytest.raises(UnknownProvider):
            build_provider_editor(p.pk, kind="carrier_pigeon", can_change=True)

    def test_a_provider_of_an_unknown_kind_keeps_it(self):
        p = _provider("odd", kind="carrier_pigeon")

        editor = build_provider_editor(p.pk, kind="carrier_pigeon", can_change=True)

        assert editor.form.provider_label == "A provider this hub doesn't have"

    def test_carried_fields_open_the_form_unbound(self):
        p = _provider("main")

        editor = build_provider_editor(
            p.pk, kind="openai", can_change=True, carried={"name": "renamed"}
        )

        assert not editor.form.is_bound
        assert editor.form.initial["name"] == "renamed"
        assert editor.form.kind == "openai"


class TestTurningItOn:
    def test_another_provider_on_is_named_under_on(self):
        _provider("main", is_active=True)
        spare = _provider("spare", kind="openai")

        for editor in (
            build_provider_editor(spare.pk, can_change=True),
            build_provider_editor(None, kind="openai", can_change=True),
        ):
            help_text = editor.form.fields["is_active"].help_text
            assert help_text == "Turning this on turns off main."

    def test_the_provider_in_use_gets_no_note(self):
        main = _provider("main", is_active=True)

        editor = build_provider_editor(main.pk, can_change=True)

        assert editor.form.fields["is_active"].help_text == ""

    def test_with_none_on_there_is_no_note(self):
        spare = _provider("spare")

        editor = build_provider_editor(spare.pk, can_change=True)

        assert editor.form.fields["is_active"].help_text == ""


class TestTheDeletion:
    def test_a_missing_provider_is_none(self):
        assert build_provider_deletion(999) is None

    def test_deleting_the_one_in_use_falls_back(self):
        main = _provider("main", is_active=True)

        deletion = build_provider_deletion(main.pk)

        assert deletion.name == "main"
        assert deletion.consequence == "Analysis falls back to the built-in local analyzer."
        assert deletion.url == f"/console/providers/{main.pk}/delete/"
        assert deletion.back_url == f"/console/providers/{main.pk}/"

    def test_deleting_a_spare_keeps_the_one_in_use(self):
        _provider("main", is_active=True)
        spare = _provider("spare", kind="openai")

        assert build_provider_deletion(spare.pk).consequence == "Analysis keeps using main."

    def test_deleting_a_spare_with_none_on_falls_back(self):
        spare = _provider("spare")

        assert build_provider_deletion(spare.pk).consequence == (
            "Analysis falls back to the built-in local analyzer."
        )

    def test_the_editor_offers_it_only_for_an_existing_provider(self):
        p = _provider("main")

        existing = build_provider_editor(p.pk, can_change=True)

        assert existing.delete == Action("Delete provider", "delete_provider", {"pk": p.pk})
        assert build_provider_editor(None, kind="claude", can_change=True).delete is None
