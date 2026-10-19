import socket
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.db import IntegrityError, OperationalError
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries.providers import build_provider_editor
from apps.intelligence._tests.test_forms import KEY, PRIVATE, STORED, initial_data
from apps.intelligence.forms import ProviderForm
from apps.intelligence.models import IntelligenceProvider
from apps.intelligence.saving import StaleProvider

pytestmark = pytest.mark.django_db

STALE = "This provider changed since you opened it. Here it is now."
UNKNOWN_VALUE = "unknown-key-value-123"
TYPED = "sk-typed-but-not-saved-987"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    def resolve(host, port, *args, **kwargs):
        address = PRIVATE.get(host, "93.184.216.34")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.fixture
def provider():
    return IntelligenceProvider.objects.create(
        name="main", provider="claude", config={**STORED["claude"], "legacy": UNKNOWN_VALUE}
    )


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="intelligence", codename=codename)
        )
    return user


@pytest.fixture
def provider_viewer(bare_staff):
    return _grant(bare_staff, "view_intelligenceprovider")


@pytest.fixture
def provider_editor(bare_staff):
    return _grant(bare_staff, "change_intelligenceprovider")


@pytest.fixture
def provider_adder(bare_staff):
    return _grant(bare_staff, "view_intelligenceprovider", "add_intelligenceprovider")


def _url(provider=None, kind=None):
    if provider is None:
        url = reverse("console:provider-new")
    else:
        url = reverse("console:provider", args=[provider.pk])
    return f"{url}?provider={kind}" if kind else url


def _data(row=None, start="claude", **overrides):
    """What the page posts back opened and saved untouched."""
    form = ProviderForm(provider=row, kind=None if row else start)
    data = initial_data(form)
    if row is not None:
        data["digest"] = build_provider_editor(row.pk, can_change=True).digest
    data.update(overrides)
    return data


def _secrets_absent(body, *extra):
    for value in (KEY, UNKNOWN_VALUE, *extra):
        assert value not in body


def test_the_editor_urls():
    assert reverse("console:provider-new") == "/console/providers/new/"
    assert reverse("console:provider", args=[7]) == "/console/providers/7/"


def test_anonymous_is_sent_to_the_admin_login(client, provider):
    resp = client.get(_url(provider))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(provider)}"


def test_an_anonymous_save_logs_in_back_to_the_editor(client, provider):
    resp = client.post(_url(provider), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(provider)}"


def test_an_anonymous_add_logs_in_back_to_the_new_provider_page(client):
    resp = client.post(_url(kind="claude"), {})

    assert resp["Location"] == f"{reverse('admin:login')}?next={_url()}"


def test_staff_without_a_provider_permission_is_refused(client, bare_staff, provider):
    assert client.get(_url(provider)).status_code == 403


def test_the_editor_renders_the_provider(client, superuser, provider):
    client.force_login(superuser)

    resp = client.get(_url(provider))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/provider.html"
    assert "<title>Provider · main · Console</title>" in body
    assert '<h1 class="c-pagehead__title">Provider main</h1>' in body
    assert f'action="/console/providers/{provider.pk}/?provider=claude"' in body
    assert 'name="digest" value="' in body
    assert 'name="name" value="main"' in body
    assert '<option value="claude" selected>Claude (Anthropic)</option>' in body
    assert 'name="switch_provider"' in body and "Show this provider's settings" in body
    assert body.index('id="provider-default"') < body.index('id="provider-switch"')
    assert body.index('id="provider-switch"') < body.index('id="provider-save"')
    assert 'type="password" name="cfg__api_key"' in body
    assert 'name="cfg__model" value="claude-sonnet-4-20250514"' in body
    assert "Set. Leave blank to keep it." in body
    assert "data-disable-on-submit" in body
    assert (
        f'<a id="provider-back" class="c-crumb" href="/console/providers/#provider-{provider.pk}"'
        in body
    )
    assert 'href="/console/providers/" aria-current="page">Providers</a>' in body
    assert "data-poll" not in body[body.index("<main") :]


def test_the_editor_reads_in_a_fixed_number_of_queries(
    client, superuser, provider, django_assert_num_queries
):
    client.force_login(superuser)
    client.get(_url(provider))

    with django_assert_num_queries(4):
        assert client.get(_url(provider)).status_code == 200


def test_no_secret_or_unknown_value_reaches_the_page(client, superuser, provider):
    client.force_login(superuser)

    body = client.get(_url(provider)).content.decode()

    _secrets_absent(body)
    assert "Saving keeps the settings this provider doesn't declare." in body


@pytest.mark.parametrize("kind", sorted(STORED))
def test_no_kind_shows_its_secrets(client, superuser, kind):
    client.force_login(superuser)
    p = IntelligenceProvider.objects.create(name="x", provider=kind, config=STORED[kind])

    body = client.get(_url(p)).content.decode()

    _secrets_absent(body)


def test_the_details_hold_the_facts(client, superuser, provider):
    client.force_login(superuser)

    body = client.get(_url(provider)).content.decode()

    details = body[body.index('id="provider-details"') :]
    assert "<dt>Provider</dt><dd>claude</dd>" in details
    assert "<dt>Settings this provider doesn&#x27;t declare</dt><dd>legacy</dd>" in details
    assert "<dt>Created</dt>" in details and "<dt>Updated</dt>" in details


def test_htmx_gets_the_editor_without_the_layout(client, superuser, provider):
    client.force_login(superuser)

    resp = client.get(_url(provider), HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_provider.html"
    assert "<html" not in resp.content.decode()


def test_the_page_carries_nothing_the_csp_blocks(client, superuser, provider):
    client.force_login(superuser)

    body = client.get(_url(provider)).content.decode()

    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_a_view_only_reader_sees_the_form_disabled(client, provider_viewer, provider):
    resp = client.get(_url(provider))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'name="name" value="main"' in body
    assert body.count(" disabled") >= 5
    assert 'id="provider-save"' not in body
    assert 'id="provider-default"' not in body
    assert 'id="provider-switch"' not in body
    assert ">Back to providers</a>\n</form>" in body
    _secrets_absent(body)


def test_a_view_only_reader_is_refused_a_save(client, provider_viewer, provider):
    resp = client.post(_url(provider), _data(provider, name="renamed"))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to change providers required."
    provider.refresh_from_db()
    assert provider.name == "main"


def test_a_change_only_user_opens_the_editor_enabled(client, provider_editor, provider):
    body = client.get(_url(provider)).content.decode()

    assert 'id="provider-save"' in body


def test_an_untouched_save_keeps_the_config(client, superuser, provider):
    client.force_login(superuser)
    stored = dict(provider.config)

    resp = client.post(_url(provider), _data(provider, name="main-2"))

    assert resp.status_code == 303
    assert resp["Location"] == f"/console/providers/{provider.pk}/"
    provider.refresh_from_db()
    assert (provider.name, provider.config) == ("main-2", stored)
    assert "Provider saved." in client.get(resp["Location"]).content.decode()


def test_a_new_secret_is_written(client, superuser, provider):
    client.force_login(superuser)

    client.post(_url(provider), _data(provider, cfg__api_key="sk-new"))

    provider.refresh_from_db()
    assert provider.config["api_key"] == "sk-new"


def test_an_invalid_save_re_renders_without_secrets(client, superuser, provider):
    client.force_login(superuser)

    resp = client.post(_url(provider), _data(provider, name="", cfg__api_key=TYPED, digest="kept"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "This field is required." in body
    assert 'name="digest" value="kept"' in body
    _secrets_absent(body, TYPED)
    provider.refresh_from_db()
    assert provider.name == "main"


def test_an_unsafe_host_is_a_field_error(client, superuser):
    client.force_login(superuser)
    grok = IntelligenceProvider.objects.create(name="grok", provider="grok", config=STORED["grok"])

    resp = client.post(_url(grok), _data(grok, cfg__base_url="http://internal.example.com/v1"))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "This hub won&#x27;t call that address." in body
    _secrets_absent(body)
    grok.refresh_from_db()
    assert grok.config == STORED["grok"]


def test_saving_with_on_ticked_turns_the_old_provider_off(client, superuser, provider):
    client.force_login(superuser)
    old = IntelligenceProvider.objects.create(name="old", provider="openai", is_active=True)

    body = client.get(_url(provider)).content.decode()
    assert "Turning this on turns off old." in body
    resp = client.post(_url(provider), _data(provider, is_active="on"))

    assert resp.status_code == 303
    assert list(IntelligenceProvider.objects.filter(is_active=True)) == [provider]
    old.refresh_from_db()
    assert old.is_active is False
    assert "Turning this on turns off" not in client.get(_url(provider)).content.decode()


def test_a_stale_save_is_a_409_with_the_fresh_values(client, superuser, provider):
    client.force_login(superuser)
    opened = _data(provider, description="mine")
    IntelligenceProvider.objects.filter(pk=provider.pk).update(description="theirs")

    resp = client.post(_url(provider), opened)

    body = resp.content.decode()
    assert resp.status_code == 409
    assert STALE in body
    assert "theirs</textarea>" in body
    _secrets_absent(body)
    provider.refresh_from_db()
    assert provider.description == "theirs"


def test_a_busy_database_is_a_409(client, superuser, provider):
    client.force_login(superuser)
    with mock.patch(
        "apps.console.views.provider.save_provider",
        side_effect=OperationalError("database is locked"),
    ):
        resp = client.post(_url(provider), _data(provider, description="x"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert "Couldn&#x27;t save just now. Here&#x27;s the provider as it is." in body
    _secrets_absent(body)


def test_a_name_another_provider_has_is_refused(client, superuser, provider):
    client.force_login(superuser)
    IntelligenceProvider.objects.create(name="spare", provider="openai")

    resp = client.post(_url(provider), _data(provider, name="spare"))

    assert resp.status_code == 200
    assert "Another provider already has this name." in resp.content.decode()


def test_a_name_taken_in_a_race_is_a_name_error(client, superuser, provider):
    client.force_login(superuser)
    IntelligenceProvider.objects.create(name="spare", provider="openai")

    with mock.patch("apps.intelligence.forms.ProviderForm.clean_name", lambda self: "spare"):
        resp = client.post(_url(provider), _data(provider, name="spare"))

    assert resp.status_code == 200
    assert "Another provider already has this name." in resp.content.decode()
    provider.refresh_from_db()
    assert provider.name == "main"


def test_an_integrity_error_without_a_clashing_name_is_a_stale_save(client, superuser, provider):
    client.force_login(superuser)
    with mock.patch(
        "apps.console.views.provider.save_provider", side_effect=IntegrityError("constraint")
    ):
        resp = client.post(_url(provider), _data(provider, name="spare"))

    body = resp.content.decode()
    assert resp.status_code == 409
    assert STALE in body
    assert "Another provider already has this name." not in body
    _secrets_absent(body)


class TestSwitchingKind:
    def test_it_re_renders_for_the_new_kind_and_saves_nothing(self, client, superuser, provider):
        client.force_login(superuser)
        data = _data(provider, name="renamed", provider="ollama", switch_provider="1")
        data["cfg__max_tokens"] = "garbage"

        resp = client.post(_url(provider), data)

        body = resp.content.decode()
        assert resp.status_code == 200
        assert f'action="/console/providers/{provider.pk}/?provider=ollama"' in body
        assert 'name="name" value="renamed"' in body
        assert '<option value="ollama" selected>Ollama (Local)</option>' in body
        assert 'name="cfg__host"' in body
        assert 'name="cfg__api_key"' not in body
        assert "c-field__error" not in body
        _secrets_absent(body)
        provider.refresh_from_db()
        assert (provider.name, provider.provider) == ("main", "claude")

    def test_the_switched_form_saves_on_the_new_kind(self, client, superuser, provider):
        client.force_login(superuser)
        switched = client.post(
            _url(provider), _data(provider, provider="openai", switch_provider="1")
        )
        editor = switched.context["editor"]
        data = initial_data(editor.form)
        data.update(digest=editor.digest, cfg__api_key="sk-openai")

        resp = client.post(_url(provider, kind="openai"), data)

        assert resp.status_code == 303
        provider.refresh_from_db()
        assert provider.provider == "openai"
        assert provider.config["api_key"] == "sk-openai"

    def test_on_the_kind_shown_it_keeps_the_edits(self, client, superuser, provider):
        client.force_login(superuser)

        resp = client.post(
            _url(provider, kind="claude"),
            _data(provider, name="", cfg__timeout_s="7", switch_provider="1"),
        )

        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'name="cfg__timeout_s" value="7"' in body
        assert "c-field__error" not in body and "aria-invalid" not in body
        _secrets_absent(body)
        provider.refresh_from_db()
        assert provider.name == "main"

    def test_an_unknown_kind_is_a_400(self, client, superuser, provider):
        client.force_login(superuser)

        resp = client.post(_url(provider), _data(provider, provider="nope", switch_provider="1"))

        body = resp.content.decode()
        assert resp.status_code == 400
        assert "This hub has no provider by that name. Pick one it has." in body
        assert '<option value="claude" selected>Claude (Anthropic)</option>' in body
        _secrets_absent(body)
        provider.refresh_from_db()
        assert provider.provider == "claude"

    def test_a_switch_on_a_deleted_provider_is_the_404(self, client, superuser, provider):
        client.force_login(superuser)
        url, data = _url(provider), _data(provider, switch_provider="1")
        provider.delete()

        assert client.post(url, data).status_code == 404

    def test_a_same_kind_switch_on_a_deleted_provider_is_the_404(self, client, superuser, provider):
        client.force_login(superuser)
        url, data = _url(provider, kind="claude"), _data(provider, switch_provider="1")
        provider.delete()

        assert client.post(url, data).status_code == 404


def test_an_unknown_kind_in_the_url_is_a_400(client, superuser, provider):
    client.force_login(superuser)

    resp = client.get(_url(provider, kind="nope"))

    body = resp.content.decode()
    assert resp.status_code == 400
    assert "This hub has no provider by that name." in body
    _secrets_absent(body)


def test_an_unknown_kind_on_a_save_saves_nothing(client, superuser, provider):
    client.force_login(superuser)

    resp = client.post(_url(provider, kind="nope"), _data(provider, name="renamed"))

    assert resp.status_code == 400
    _secrets_absent(resp.content.decode())
    provider.refresh_from_db()
    assert provider.name == "main"


class TestAProviderOfAKindThisHubDoesntHave:
    @pytest.fixture
    def odd(self):
        return IntelligenceProvider.objects.create(
            name="odd", provider="carrier_pigeon", config={"coop": UNKNOWN_VALUE}
        )

    def test_it_renders_with_no_typed_fields(self, client, superuser, odd):
        client.force_login(superuser)

        body = client.get(_url(odd)).content.decode()

        assert '<option value="carrier_pigeon" selected>A provider this hub doesn' in body
        assert 'name="cfg__' not in body
        assert 'id="provider-settings"' not in body
        assert "<dt>Settings this provider doesn&#x27;t declare</dt><dd>coop</dd>" in body
        _secrets_absent(body)

    def test_saving_keeps_its_config(self, client, superuser, odd):
        client.force_login(superuser)

        resp = client.post(_url(odd), _data(odd, description="kept"))

        assert resp.status_code == 303
        odd.refresh_from_db()
        assert (odd.config, odd.description) == ({"coop": UNKNOWN_VALUE}, "kept")


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_provider_is_the_console_404_page(client, superuser, method):
    client.force_login(superuser)

    resp = getattr(client, method)(reverse("console:provider", args=[999]))

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This provider doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/providers/">Back to providers</a>' in body


def test_a_bad_kind_on_a_missing_provider_is_the_404(client, superuser):
    client.force_login(superuser)

    assert client.get(_url(None) + "999/?provider=nope").status_code == 404


def test_a_provider_deleted_during_the_save_answers_the_404(client, superuser, provider):
    client.force_login(superuser)
    editors = [build_provider_editor(provider.pk, _data(provider), can_change=True), None]

    with (
        mock.patch("apps.console.views.provider.build_provider_editor", side_effect=editors),
        mock.patch("apps.console.views.provider.save_provider", side_effect=StaleProvider),
    ):
        resp = client.post(_url(provider), _data(provider))

    assert resp.status_code == 404


class TestANewProvider:
    def test_without_a_kind_it_lists_the_kinds(self, client, provider_adder):
        resp = client.get(_url())

        body = resp.content.decode()
        assert resp.status_code == 200
        assert resp.templates[0].name == "console/provider_new.html"
        assert "<title>New provider · Console</title>" in body
        for key, label in IntelligenceProvider.PROVIDER_CHOICES:
            assert f'href="/console/providers/new/?provider={key}">{label}</a>' in body
        assert "?provider=local" not in body
        assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]

    def test_a_kind_opens_its_form(self, client, provider_adder):
        resp = client.get(_url(kind="ollama"))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert '<h1 class="c-pagehead__title">New provider</h1>' in body
        assert 'action="/console/providers/new/?provider=ollama"' in body
        assert 'name="cfg__host"' in body
        assert 'id="provider-details"' not in body

    def test_an_unknown_kind_is_a_400_with_the_list(self, client, provider_adder):
        resp = client.get(_url(kind="nope"))

        body = resp.content.decode()
        assert resp.status_code == 400
        assert "This hub has no provider by that name. Pick one it has." in body
        assert 'href="/console/providers/new/?provider=claude"' in body

    def test_a_save_without_a_kind_is_a_400(self, client, provider_adder):
        resp = client.post(_url(), _data(name="spare"))

        assert resp.status_code == 400
        assert not IntelligenceProvider.objects.exists()

    def test_without_add_permission_it_is_refused(self, client, provider_viewer):
        assert client.get(_url()).status_code == 403
        assert client.get(_url(kind="claude")).status_code == 403
        assert client.post(_url(kind="claude"), _data(name="spare")).status_code == 403
        assert not IntelligenceProvider.objects.exists()

    def test_a_save_creates_it(self, client, provider_adder):
        resp = client.post(
            _url(kind="claude"), _data(name="spare", cfg__api_key=TYPED, description="d")
        )

        p = IntelligenceProvider.objects.get(name="spare")
        assert resp.status_code == 303
        assert resp["Location"] == f"/console/providers/{p.pk}/"
        assert (p.provider, p.description, p.is_active) == ("claude", "d", False)
        assert p.config == {"api_key": TYPED}

    def test_creating_one_on_turns_the_old_one_off(self, client, provider_adder, provider):
        IntelligenceProvider.objects.filter(pk=provider.pk).update(is_active=True)

        resp = client.post(_url(kind="openai"), _data(start="openai", name="new", is_active="on"))

        assert resp.status_code == 303
        assert list(IntelligenceProvider.objects.filter(is_active=True).values_list("name")) == [
            ("new",)
        ]

    def test_an_invalid_one_saves_nothing(self, client, provider_adder):
        resp = client.post(_url(kind="claude"), _data(name="", cfg__api_key=TYPED))

        body = resp.content.decode()
        assert resp.status_code == 200
        assert "This field is required." in body
        _secrets_absent(body, TYPED)
        assert not IntelligenceProvider.objects.exists()

    def test_a_switch_saves_nothing(self, client, provider_adder):
        resp = client.post(
            _url(kind="claude"), _data(name="spare", provider="openai", switch_provider="1")
        )

        body = resp.content.decode()
        assert resp.status_code == 200
        assert 'action="/console/providers/new/?provider=openai"' in body
        assert 'name="name" value="spare"' in body
        assert not IntelligenceProvider.objects.exists()

    def test_an_unknown_switch_is_a_400_with_the_list(self, client, provider_adder):
        resp = client.post(_url(kind="claude"), _data(provider="nope", switch_provider="1"))

        assert resp.status_code == 400
        assert resp.templates[0].name == "console/provider_new.html"

    def test_a_name_taken_in_a_race_is_a_name_error(self, client, provider_adder, provider):
        with (
            mock.patch("apps.intelligence.forms.ProviderForm.clean_name", lambda self: "main"),
            mock.patch(
                "apps.console.views.provider.IntelligenceProvider.objects.create",
                side_effect=IntegrityError("UNIQUE constraint"),
            ),
        ):
            resp = client.post(_url(kind="claude"), _data(name="main"))

        assert resp.status_code == 200
        assert "Another provider already has this name." in resp.content.decode()

    @pytest.mark.parametrize("error", [IntegrityError("active"), OperationalError("locked")])
    def test_any_other_failure_is_a_409_that_saves_nothing(self, client, provider_adder, error):
        with mock.patch(
            "apps.console.views.provider.IntelligenceProvider.objects.create", side_effect=error
        ):
            resp = client.post(_url(kind="claude"), _data(name="spare", cfg__api_key=TYPED))

        body = resp.content.decode()
        assert resp.status_code == 409
        assert "Couldn&#x27;t add the provider just now." in body
        assert 'name="name" value="spare"' in body
        _secrets_absent(body, TYPED)
        assert not IntelligenceProvider.objects.exists()

    def test_adding_lands_with_a_message(self, client, provider_adder):
        resp = client.post(_url(kind="claude"), _data(name="spare"))

        assert "Provider added." in client.get(resp["Location"]).content.decode()
