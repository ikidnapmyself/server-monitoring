import re

import pytest
from django.urls import reverse

from apps.console._tests.test_guard import visible
from apps.console.models import AuditEntry
from apps.orchestration.models import PipelineDefinition

pytestmark = pytest.mark.django_db

URL = "/console/activity/"
SECRET = "https://hooks.slack.com/services/T0/B0/never-shown"


def _entry(**fields):
    defaults = {
        "action": "route.edit",
        "target_kind": "route",
        "target_id": 1,
        "target_name": "web",
    }
    return AuditEntry.objects.create(**{**defaults, **fields})


def test_the_url():
    assert reverse("console:activity") == URL


def test_anonymous_is_sent_to_the_console_login(client):
    resp = client.get(URL)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={URL}"


def test_active_staff_who_arent_superusers_are_refused(client, active_staff):
    assert client.get(URL).status_code == 403


def test_the_page_is_the_activity_tab_after_users(client, superuser):
    body = client.get(URL).content.decode()

    nav = body[body.index('aria-label="System"') :]
    nav = nav[: nav.index("</nav>")]
    assert re.findall(r">([^<>]+)</a>", nav)[-2:] == ["Users", "Activity"]
    assert 'href="/console/activity/" aria-current="page">Activity' in nav
    assert "<title>Activity · Console</title>" in body


def test_a_row_says_who_did_what_and_links_the_target(client, superuser):
    route = PipelineDefinition.objects.create(name="web", stages=["notify"], match=[])
    entry = _entry(
        target_id=route.pk,
        actor=superuser,
        actor_name="root",
        changes={"priority": {"old": 50, "new": 60}},
    )

    body = client.get(URL).content.decode()

    assert (
        f'<a id="entry-{entry.pk}-target" href="/console/routes/{route.pk}/">route web</a>' in body
    )
    assert f'<a id="entry-{entry.pk}-actor" href="/console/users/{superuser.pk}/">root</a>' in body
    text = " ".join(visible(body).split())
    assert "Edited route web" in text
    assert "Priority 50 60" in text


def test_a_secret_renders_as_changed_and_never_its_value(client, superuser):
    _entry(
        action="channel.edit",
        target_kind="channel",
        changes={"config.webhook_url": {"changed": True}},
        actor_name="gone",
    )

    body = client.get(URL).content.decode()

    assert SECRET not in body
    assert "Webhook url changed changed" in " ".join(visible(body).split())
    assert "entry-" in body and '-actor">gone</span>' in body


def test_a_result_renders_as_a_sentence(client, superuser):
    entry = _entry(
        action="inbox.drain",
        target_kind="run",
        target_name="",
        target_id=34,
        changes={"result": {"ran": 2, "failed": 0}},
    )

    body = client.get(URL).content.decode()

    assert f'<p id="entry-{entry.pk}-summary">2 runs drained.</p>' in body
    assert f"entry-{entry.pk}-changes" not in body
    assert "Drained run 34" in " ".join(visible(body).split())


def test_a_delete_renders_its_last_values(client, superuser):
    entry = _entry(
        action="route.delete",
        changes={"name": {"old": "web", "new": None}, "priority": {"old": 50, "new": None}},
    )

    body = client.get(URL).content.decode()

    assert f'id="entry-{entry.pk}-last"' in body
    assert f"entry-{entry.pk}-changes" not in body
    assert "Deleted route web Not signed in" in " ".join(visible(body).split())


def test_the_filters_are_selects_that_keep_their_choice(client, superuser):
    _entry(actor=superuser, actor_name="root", action="key.edit", target_kind="key")

    body = client.get(f"{URL}?actor={superuser.pk}&kind=key").content.decode()

    assert f'<option value="{superuser.pk}" selected>root</option>' in body
    assert '<option value="key" selected>API key</option>' in body


def test_htmx_gets_the_list_without_the_layout(client, superuser):
    resp = client.get(URL, HTTP_HX_REQUEST="true")

    assert resp.templates[0].name == "console/partials/_activity.html"
    assert "<title>" not in resp.content.decode()


def test_an_empty_trail_and_an_empty_match_say_so(client, superuser):
    AuditEntry.objects.all().delete()
    assert "Nothing recorded yet." in client.get(URL).content.decode()
    _entry()
    assert "Nothing matches these filters." in client.get(f"{URL}?kind=key").content.decode()


def test_more_than_a_page_paginates(client, superuser, monkeypatch):
    monkeypatch.setattr("apps.console.queries.activity.PAGE_SIZE", 1)
    AuditEntry.objects.all().delete()
    _entry()
    _entry()

    body = client.get(URL).content.decode()

    assert "Page 1 of 2" in body
    assert 'id="page-next" href="/console/activity/?page=2"' in body
