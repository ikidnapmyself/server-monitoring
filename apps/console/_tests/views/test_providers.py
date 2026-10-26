import re
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.console._tests.test_templates import FORBIDDEN
from apps.intelligence._tests.test_forms import KEY, STORED
from apps.intelligence.models import IntelligenceProvider

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def url():
    return reverse("console:providers")


@pytest.fixture
def provider_viewer(bare_staff):
    bare_staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="intelligence", codename="view_intelligenceprovider"
        )
    )
    return bare_staff


def _provider(name, kind="claude", **kw):
    return IntelligenceProvider.objects.create(name=name, provider=kind, **kw)


def test_the_page_lives_at_providers(url):
    assert url == "/console/providers/"


def test_anonymous_is_sent_to_the_console_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next=/console/providers/"


def test_staff_without_a_provider_permission_is_refused(client, bare_staff, url):
    assert client.get(url).status_code == 403


def test_providers_render_in_use_first_with_kind_and_state(client, superuser, url):
    client.force_login(superuser)
    main = _provider("main", is_active=True, config=STORED["claude"], description="Paid plan")
    _provider("backup", kind="openai", config=STORED["openai"])

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/providers.html"
    assert "<title>Providers · Console</title>" in body
    assert body.index(">main</a>") < body.index(">backup</a>")
    assert f'<a id="provider-{main.pk}-edit" href="/console/providers/{main.pk}/">main</a>' in body
    assert '<span class="c-label">Claude (Anthropic)</span>' in body
    assert '<span class="c-label">OpenAI</span>' in body
    assert '<span class="c-label c-tone-ok">In use</span>' in body
    assert '<span class="c-label c-tone-muted">Off</span>' in body
    assert '<span class="c-row__icon c-tone-ok">' in body
    assert '<span class="c-row__icon c-tone-muted">' in body
    assert '<div class="c-row__meta">Paid plan</div>' in body
    assert body.count('class="c-row__meta"') == 1
    assert '<h2 id="providers-uses" class="c-box__title">Analysis uses main.</h2>' in body
    assert (
        '<a id="providers-add" class="c-button c-button--sm c-button--primary" '
        'href="/console/providers/new/">Add a provider</a>'
    ) in body
    assert KEY not in body
    assert not [label for label, rule in FORBIDDEN.items() if rule.search(body)]


def test_with_none_on_the_header_names_the_local_analyzer(client, superuser, url):
    client.force_login(superuser)
    _provider("spare")

    body = client.get(url).content.decode()

    assert "No provider is on, so analysis uses the built-in local analyzer." in body


def test_no_providers_says_so(client, superuser, url):
    client.force_login(superuser)

    body = client.get(url).content.decode()

    assert "No providers." in body
    assert "No provider is on, so analysis uses the built-in local analyzer." in body


def test_a_kind_the_hub_doesnt_have_is_named_so(client, superuser, url):
    client.force_login(superuser)
    _provider("odd", kind="carrier_pigeon")

    assert "A provider this hub doesn" in client.get(url).content.decode()


def test_a_viewer_gets_links_but_no_add(client, provider_viewer, url):
    main = _provider("main")

    body = client.get(url).content.decode()

    assert f'href="/console/providers/{main.pk}/">main</a>' in body
    assert "Add a provider" not in body


def test_a_name_without_a_link_is_plain_text(client, provider_viewer, url):
    _provider("main")

    with mock.patch("apps.console.intents.INTENTS", {}):
        body = client.get(url).content.decode()

    assert "<strong>main</strong>" in body


def test_htmx_gets_the_list_without_the_layout(client, superuser, url):
    client.force_login(superuser)

    resp = client.get(url, **HTMX)

    assert resp.templates[0].name == "console/partials/_providers.html"
    assert "<html" not in resp.content.decode()


def test_a_provider_only_reader_gets_system_on_providers_and_only_its_tab(
    client, provider_viewer, url
):
    body = client.get(url).content.decode()

    assert re.search(r'href="/console/providers/" aria-current="page">.*?<span>System</span>', body)
    assert 'href="/console/providers/" aria-current="page">Providers</a>' in body
    assert ">Routes</a>" not in body and ">Channels</a>" not in body
