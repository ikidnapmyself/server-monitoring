import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.console._tests.test_guard import visible
from apps.intelligence._tests.test_forms import KEY, STORED
from apps.intelligence.models import IntelligenceProvider

pytestmark = pytest.mark.django_db

FALLBACK = "Analysis falls back to the built-in local analyzer."


@pytest.fixture
def provider():
    return IntelligenceProvider.objects.create(
        name="main", provider="claude", config=STORED["claude"]
    )


def _grant(user, *codenames):
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="intelligence", codename=codename)
        )
    return user


@pytest.fixture
def provider_editor(bare_staff):
    return _grant(bare_staff, "change_intelligenceprovider")


@pytest.fixture
def provider_deleter(bare_staff):
    return _grant(bare_staff, "change_intelligenceprovider", "delete_intelligenceprovider")


def _url(provider):
    return reverse("console:provider-delete", args=[provider.pk])


def test_the_delete_url():
    assert reverse("console:provider-delete", args=[7]) == "/console/providers/7/delete/"


def test_anonymous_is_sent_to_the_console_login(client, provider):
    resp = client.get(_url(provider))

    assert resp.status_code == 302
    assert resp.url.startswith(reverse("console:login"))


def test_an_anonymous_delete_logs_in_back_to_the_editor(client, provider):
    resp = client.post(_url(provider))

    assert resp.status_code == 302
    assert f"next=/console/providers/{provider.pk}/" in resp.url
    assert IntelligenceProvider.objects.filter(pk=provider.pk).exists()


def test_the_page_asks_and_says_what_analysis_falls_back_to(client, superuser, provider):
    IntelligenceProvider.objects.filter(pk=provider.pk).update(is_active=True)
    client.force_login(superuser)

    resp = client.get(_url(provider))

    body = resp.content.decode()
    text = visible(body)
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/provider_delete.html"
    assert "<title>Delete provider · main · Console</title>" in body
    assert "Delete provider main?" in text
    assert FALLBACK in text
    form = body[body.index(f'<form method="post" action="{_url(provider)}"') :]
    assert "data-disable-on-submit" in form[:100]
    assert '<button id="provider-delete-confirm"' in form and "Delete provider</button>" in form
    assert (
        f'<a id="provider-delete-cancel" class="c-button" href="/console/providers/{provider.pk}/"'
        in form
    )
    assert KEY not in body
    assert 'href="/console/providers/" aria-current="page">Providers</a>' in body
    assert IntelligenceProvider.objects.filter(pk=provider.pk).exists()


def test_deleting_a_spare_says_analysis_keeps_the_one_in_use(client, superuser, provider):
    IntelligenceProvider.objects.create(name="other", provider="openai", is_active=True)
    client.force_login(superuser)

    text = visible(client.get(_url(provider)).content.decode())

    assert "Analysis keeps using other." in text
    assert FALLBACK not in text


def test_deleting_a_spare_with_none_on_says_the_fallback(client, superuser, provider):
    client.force_login(superuser)

    assert FALLBACK in visible(client.get(_url(provider)).content.decode())


def test_htmx_gets_the_page_without_the_layout(client, superuser, provider):
    client.force_login(superuser)

    resp = client.get(_url(provider), headers={"HX-Request": "true"})

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_provider_delete.html"
    assert "Delete provider main?" in body
    assert "<html" not in body


def test_a_post_deletes_and_lands_on_the_providers_page(client, superuser, provider):
    client.force_login(superuser)

    resp = client.post(_url(provider))

    assert resp.status_code == 303
    assert resp.url == reverse("console:providers")
    assert not IntelligenceProvider.objects.filter(pk=provider.pk).exists()
    assert "Provider main deleted." in client.get(resp.url).content.decode()


def test_a_delete_permission_is_enough(client, provider_deleter, provider):
    assert client.get(_url(provider)).status_code == 200
    assert client.post(_url(provider)).status_code == 303


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_change_only_user_is_refused(client, provider_editor, provider, method):
    resp = getattr(client, method)(_url(provider))

    assert resp.status_code == 403
    assert resp.content.decode() == "Permission to delete providers required."
    assert IntelligenceProvider.objects.filter(pk=provider.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_delete_only_user_is_refused(client, bare_staff, provider, method):
    _grant(bare_staff, "delete_intelligenceprovider")

    resp = getattr(client, method)(_url(provider))

    assert resp.status_code == 403
    assert IntelligenceProvider.objects.filter(pk=provider.pk).exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_missing_provider_is_the_console_404_page(client, superuser, method):
    client.force_login(superuser)

    resp = getattr(client, method)(reverse("console:provider-delete", args=[999]))

    assert resp.status_code == 404
    assert "This provider doesn't exist. It may have been deleted." in resp.content.decode()


class TestTheEditorLink:
    def test_a_deleter_sees_it(self, client, provider_deleter, provider):
        body = client.get(reverse("console:provider", args=[provider.pk])).content.decode()

        assert (
            f'<a id="provider-delete" class="c-button c-button--sm c-button--danger" '
            f'href="{_url(provider)}">Delete provider</a>'
        ) in body

    def test_a_change_only_user_does_not(self, client, provider_editor, provider):
        body = client.get(reverse("console:provider", args=[provider.pk])).content.decode()

        assert 'id="provider-delete"' not in body

    def test_a_new_provider_has_none(self, client, superuser):
        client.force_login(superuser)

        body = client.get(f"{reverse('console:provider-new')}?provider=claude").content.decode()

        assert 'id="provider-delete"' not in body
