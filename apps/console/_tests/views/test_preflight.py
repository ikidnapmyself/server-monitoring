import re

import pytest
from django.urls import reverse

from apps.alerts.models import Node
from apps.console._tests.queries.test_preflight import _preflight
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_checks import REPORTS_CURRENT, _nav, _tag
from apps.console._tests.views.test_incident import focus_audit

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
LIST = "/console/preflight/"


def _record(run):
    return reverse("console:preflight-run", args=[run.pk])


def test_the_routes():
    assert reverse("console:preflight") == LIST
    assert reverse("console:preflight-run", args=[7]) == "/console/preflight/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(LIST)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={LIST}"


@pytest.mark.django_db
def test_a_superuser_reads_both_pages(client, superuser):
    run = _preflight("warn", warnings=2)

    listing = client.get(LIST)
    record = client.get(_record(run))

    body = listing.content.decode()
    assert listing.status_code == record.status_code == 200
    assert listing.templates[0].name == "console/preflight.html"
    assert record.templates[0].name == "console/preflight_run.html"
    assert "<title>Preflight · Console</title>" in body
    assert "<title>Preflight found 2 warnings · Console</title>" in record.content.decode()
    assert _nav(body, "Reports") == REPORTS_CURRENT
    assert f'<a class="c-subnav__item" href="{LIST}" aria-current="page">Preflight</a>' in body


@pytest.mark.django_db
def test_a_full_reader_sees_every_tab_and_reports_opens_the_summary(client, superuser):

    body = client.get(LIST).content.decode()

    assert _nav(body, "Reports") == REPORTS_CURRENT
    assert f'<a class="c-subnav__item" href="{reverse("console:checks")}">Checks</a>' in body


@pytest.mark.django_db
def test_the_partial_is_the_polled_root(client, superuser):
    resp = client.get(LIST, **HTMX)

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_preflight.html"
    root = _tag(body, "preflight")
    assert body.lstrip().startswith('<div id="preflight"')
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{LIST}"' in root and 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_rows_link_to_the_run_with_their_meta(client, superuser):
    Node.objects.create(instance_id="i-hub", hostname="hub")
    run = _preflight("error", errors=1)
    bare = _preflight(instance_id="", triggered_by="")

    body = client.get(LIST).content.decode()

    assert f'<a id="preflight-{run.pk}" href="/console/preflight/{run.pk}/">' in body
    assert re.search(
        r"From the command line · <span class=\"c-host\">.*?</svg>hub</span> · <time", body
    )
    assert re.search(rf'id="preflight-{bare.pk}"[^<]*</a>\s*<div class="c-row__meta"><time', body)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [
        (False, "", "No preflight runs yet."),
        (True, "?status=error", "No preflight runs match these filters."),
    ],
)
def test_each_blank_state(client, superuser, setup, query, heading):
    if setup:
        _preflight()

    assert heading in client.get(f"{LIST}{query}").content.decode()


@pytest.mark.django_db
def test_an_invalid_filter_is_named(client, superuser):
    assert "Ignored invalid filter: status." in client.get(f"{LIST}?status=x").content.decode()


@pytest.mark.django_db
def test_more_than_a_page_offers_the_pager(client, superuser, monkeypatch):
    monkeypatch.setattr("apps.console.queries.preflight.PAGE_SIZE", 1)
    _preflight()
    _preflight()

    second = client.get(f"{LIST}?page=2").content.decode()

    assert 'id="page-prev"' in second and "Page 2 of 2" in second


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_run_is_the_console_404_page(client, superuser, pk, headers):
    resp = client.get(f"{LIST}{pk}/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This preflight run doesn't exist." in body
    assert f'<a id="not-found-back" href="{LIST}">Back to preflight</a>' in body


@pytest.mark.django_db
def test_the_record_groups_checks_and_links_the_node(client, superuser):
    node = Node.objects.create(instance_id="i-hub", hostname="hub")
    run = _preflight(
        "error",
        errors=1,
        checks=[("ok", "Python 3.12", ""), ("error", ".env is missing", "Copy .env.sample")],
    )

    body = client.get(_record(run)).content.decode()

    assert f'<a id="preflight-node" href="/console/nodes/{node.pk}/">hub</a>' in body
    text = visible(body)
    assert text.index("Errors") < text.index(".env is missing") < text.index("Passed")
    assert "Copy .env.sample" in text
    assert "Warnings" not in text
    assert "3 passed, 0 warnings, 1 error" in text
    assert "i-hub" not in text


@pytest.mark.django_db
def test_an_unknown_node_says_so(client, superuser):
    assert "Unknown node" in visible(client.get(_record(_preflight())).content.decode())


@pytest.mark.django_db
@BOTH
def test_hostile_text_is_escaped(client, superuser, headers):
    run = _preflight("error", checks=[("error", MARKUP, MARKUP)], triggered_by=MARKUP)

    for url in (LIST, _record(run)):
        assert "<img" not in client.get(url, **headers).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_pages_are_clean(client, superuser, label, headers):
    run = _preflight("warn", checks=[("warn", "DEBUG is on", "Set DEBUG=0")])

    for url in (LIST, _record(run)):
        assert not FORBIDDEN[label].search(client.get(url, **headers).content.decode()), url


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, headers):
    Node.objects.create(instance_id="i-hub", hostname="hub")
    run = _preflight("warn", checks=[("warn", "A", "a"), ("warn", "B", "b"), ("ok", "C", "")])
    _preflight()

    for url, root in ((LIST, '<div id="preflight"'), (_record(run), '<div id="preflight-run"')):
        body = client.get(url, **headers).content.decode()
        region = body if headers else body[body.index(root) : body.index("</main>")]
        focusable, ids = focus_audit(region)
        assert [tag for tag, element_id in focusable if not element_id] == [], url
        assert len(ids) == len(set(ids)), url
