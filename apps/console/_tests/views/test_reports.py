import re

import pytest
from django.urls import reverse

from apps.checkers.models import CheckRun
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_checks import _grant, _nav, _tag
from apps.console._tests.views.test_incident import focus_audit
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
URL = "/console/reports/"
TABS = {
    "Summary": ("orchestration", "view_pipelinerun"),
    "Checks": ("checkers", "view_checkrun"),
    "Preflight": ("checkers", "view_preflightrun"),
    "Analyses": ("intelligence", "view_analysisrun"),
}
CARDS = {
    "Summary": ("Pipeline health, last 24 hours", "Top failure reasons, last 7 days"),
    "Checks": ("Top failing checkers, last 7 days",),
    "Preflight": ("Latest preflight",),
    "Analyses": ("Provider usage, last 7 days",),
}


def _tabs(body):
    nav = body[body.index('<nav class="c-subnav" aria-label="Reports">') :]
    return re.findall(r">([A-Za-z]+)</a>", nav[: nav.index("</nav>")])


def _cards(body):
    return re.findall(r'<h2 class="c-box__header c-box__title">([^<]+)</h2>', body)


def test_the_route():
    assert reverse("console:reports") == URL


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(URL)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={URL}"


@pytest.mark.django_db
def test_staff_without_a_report_permission_is_refused(client, bare_staff):
    _grant(bare_staff, "alerts", "view_node")

    assert client.get(URL).status_code == 403
    assert _nav(client.get(reverse("console:nodes")).content.decode(), "Reports") is None


@pytest.mark.django_db
@pytest.mark.parametrize("tab", sorted(TABS))
def test_one_report_permission_shows_its_card_and_tabs_and_opens_the_summary(
    client, bare_staff, tab
):
    _grant(bare_staff, *TABS[tab])

    resp = client.get(URL)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/reports.html"
    assert "<title>Reports · Console</title>" in body
    assert _nav(body, "Reports") == f'<a class="c-unav__item" href="{URL}" aria-current="page">'
    assert _tabs(body) == (["Summary"] if tab == "Summary" else ["Summary", tab])
    assert _cards(body) == list(CARDS[tab])


@pytest.mark.django_db
def test_a_full_reader_sees_every_tab_and_card_in_order(client, superuser):
    client.force_login(superuser)

    body = client.get(URL).content.decode()

    assert _tabs(body) == ["Summary", "Checks", "Preflight", "Analyses"]
    assert f'<a class="c-subnav__item" href="{URL}" aria-current="page">Summary</a>' in body
    assert _cards(body) == [
        *CARDS["Summary"],
        *CARDS["Checks"],
        *CARDS["Analyses"],
        *CARDS["Preflight"],
        "Fleet metrics",
    ]


@pytest.mark.django_db
def test_an_empty_install_says_so_in_every_card(client, superuser):
    client.force_login(superuser)

    text = visible(client.get(URL).content.decode())

    for empty in (
        "No runs in the last 24 hours.",
        "No failed runs in the last 7 days.",
        "No failing results in the last 7 days.",
        "No analyses in the last 7 days.",
        "Preflight hasn't run on this hub yet.",
        "No node has reported a headline metric yet.",
    ):
        assert empty in text, empty


@pytest.mark.django_db
def test_rows_link_the_filtered_lists(client, superuser):
    client.force_login(superuser)
    PipelineRun.objects.create(
        run_id="r", trace_id="t", status="failed", last_error_message="no_route: x"
    )
    CheckRun.objects.create(checker_name="disk", status="critical", message="m")

    body = client.get(URL).content.decode()

    assert "100% of runs finished" not in body and "0% of runs finished" in body
    assert '<a id="report-health-2" href="/console/runs/?status=failed&amp;since=24h">' in body
    reason = _tag(body, "report-reason-1")
    assert 'href="/console/runs/?status=failed&amp;reason=no_route&amp;since=7d"' in reason
    assert "No route matches this incident</a>" in body
    checker = _tag(body, "report-checker-1")
    assert 'href="/console/checks/?status=failing&amp;checker=disk&amp;since=7d"' in checker


@pytest.mark.django_db
def test_the_reason_row_lands_on_a_matching_chip(client, superuser):
    client.force_login(superuser)
    PipelineRun.objects.create(
        run_id="r", trace_id="t", status="failed", last_error_message="no_route: x"
    )

    body = client.get("/console/runs/?status=failed&reason=no_route&since=7d").content.decode()

    assert ">No route matches this incident</a>" in body
    assert 'aria-current="true"' in _tag(body, "chip-reason-no_route")


@pytest.mark.django_db
def test_the_partial_is_the_polled_content(client, superuser):
    client.force_login(superuser)

    page = client.get(URL).content.decode()
    partial = client.get(URL, **HTMX)

    wrapper = _tag(page, "reports")
    assert re.search(r"\sdata-poll[\s>]", wrapper) and f'hx-get="{URL}"' in wrapper
    assert partial.templates[0].name == "console/partials/_reports.html"
    assert "<html" not in partial.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_page_is_clean(client, superuser, label, headers):
    client.force_login(superuser)

    assert not FORBIDDEN[label].search(client.get(URL, **headers).content.decode()), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, headers):
    client.force_login(superuser)
    PipelineRun.objects.create(run_id="r", trace_id="t", status="failed")
    CheckRun.objects.create(checker_name="disk", status="critical", message="m")

    body = client.get(URL, **headers).content.decode()
    region = body if headers else body[body.index('<div id="reports"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
