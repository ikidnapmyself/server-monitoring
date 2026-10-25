import re

import pytest
from django.urls import reverse

from apps.alerts.models import Incident
from apps.console._tests.queries.test_analyses import _analysis
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_checks import _grant, _nav, _tag
from apps.console._tests.views.test_incident import focus_audit

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
LIST = "/console/analyses/"
CONFIG = {"model": "gpt-4o", "base_url": "https://llm.example/v1"}


@pytest.fixture
def analysis_viewer(bare_staff):
    return _grant(bare_staff, "intelligence", "view_analysisrun")


def _record(run):
    return reverse("console:analysis", args=[run.pk])


def _busy():
    incident = Incident.objects.create(severity="critical", status="open", subject="disk")
    run = _analysis(
        incident=incident,
        provider_config=CONFIG,
        recommendations=[{"title": "Rotate logs", "description": "Turn on logrotate"}],
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
        duration_ms=2500,
    )
    return run, incident


def test_the_routes():
    assert reverse("console:analyses") == LIST
    assert reverse("console:analysis", args=[7]) == "/console/analyses/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(LIST)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={LIST}"


@pytest.mark.django_db
def test_staff_without_an_analysis_permission_is_refused(client, bare_staff):
    _grant(bare_staff, "intelligence", "view_intelligenceprovider")
    run = _analysis()

    assert client.get(LIST).status_code == 403
    assert client.get(_record(run)).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("codename", ["view_analysisrun", "change_analysisrun"])
def test_view_or_change_permission_reads_both_pages(client, bare_staff, codename):
    _grant(bare_staff, "intelligence", codename)
    run = _analysis()

    listing = client.get(LIST)
    record = client.get(_record(run))

    body = listing.content.decode()
    assert listing.status_code == record.status_code == 200
    assert listing.templates[0].name == "console/analyses.html"
    assert record.templates[0].name == "console/analysis.html"
    assert "<title>Analyses · Console</title>" in body
    assert "<title>Disk filled by logs · Console</title>" in record.content.decode()
    assert _nav(body, "Reports") == f'<a class="c-unav__item" href="{LIST}" aria-current="page">'
    assert f'<a class="c-subnav__item" href="{LIST}" aria-current="page">Analyses</a>' in body
    assert "Checks</a>" not in body and "Preflight</a>" not in body


@pytest.mark.django_db
def test_the_reports_item_falls_through_to_the_first_readable_tab(client, bare_staff):
    _grant(bare_staff, "checkers", "view_preflightrun")
    _grant(bare_staff, "intelligence", "view_analysisrun")

    body = client.get(LIST).content.decode()

    assert _nav(body, "Reports") == (
        f'<a class="c-unav__item" href="{reverse("console:preflight")}" aria-current="page">'
    )
    assert ">Preflight</a>" in body and ">Analyses</a>" in body


@pytest.mark.django_db
def test_the_partial_is_the_polled_root(client, analysis_viewer):
    resp = client.get(LIST, **HTMX)

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_analyses.html"
    root = _tag(body, "analyses")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{LIST}"' in root and 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_rows_show_incident_provider_and_tokens(client, superuser):
    client.force_login(superuser)
    run, incident = _busy()
    lone = _analysis("failed", "local")

    body = client.get(LIST).content.decode()

    assert f'<a id="analysis-{run.pk}" href="/console/analyses/{run.pk}/">' in body
    assert f'<a id="analysis-{run.pk}-incident" href="/console/incidents/{incident.pk}/">' in body
    assert re.search(r"</a> · OpenAI · 1,500 tokens · <time", body)
    assert f"analysis-{lone.pk}-incident" not in body
    assert "Built-in local analyzer · <time" in body


@pytest.mark.django_db
def test_an_analysis_reader_sees_the_incident_as_text(client, analysis_viewer):
    run, incident = _busy()

    body = client.get(LIST, **HTMX).content.decode()

    assert f"analysis-{run.pk}-incident" not in body
    assert "/console/incidents/" not in body


@pytest.mark.django_db
def test_the_form_carries_the_provider_and_resends_the_rest(client, analysis_viewer):
    _analysis()

    body = client.get(f"{LIST}?status=failed&since=7d&provider=openai").content.decode()

    form = body[body.index('<form class="c-search') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="status" value="failed">' in form
    assert '<input type="hidden" name="since" value="7d">' in form
    assert '<option value="openai" selected>OpenAI</option>' in form
    assert '<option value="local">Built-in local analyzer</option>' in form


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [(False, "", "No analyses yet."), (True, "?status=failed", "No analyses match these filters.")],
)
def test_each_blank_state(client, analysis_viewer, setup, query, heading):
    if setup:
        _analysis()

    assert heading in client.get(f"{LIST}{query}").content.decode()


@pytest.mark.django_db
def test_an_invalid_filter_is_named(client, analysis_viewer):
    body = client.get(f"{LIST}?status=x&provider=nobody").content.decode()

    assert "Ignored invalid filter: status, provider." in body


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_analysis_is_the_console_404_page(client, analysis_viewer, pk, headers):
    resp = client.get(f"{LIST}{pk}/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This analysis doesn't exist." in body
    assert f'<a id="not-found-back" href="{LIST}">Back to analyses</a>' in body


@pytest.mark.django_db
def test_the_record_shows_recommendations_tokens_and_the_incident(client, superuser):
    client.force_login(superuser)
    run, incident = _busy()

    body = client.get(_record(run)).content.decode()

    assert f'<a id="analysis-incident" href="/console/incidents/{incident.pk}/">' in body
    text = visible(body)
    assert "Rotate logs" in text and "Turn on logrotate" in text
    assert "1,200 in, 300 out" in text
    assert "2s" in text


@pytest.mark.django_db
@BOTH
def test_the_provider_config_renders_only_inside_details(client, analysis_viewer, headers):
    run, _ = _busy()

    body = client.get(_record(run), **headers).content.decode()

    assert "llm.example" not in visible(body)
    details = body[body.index('id="analysis-what-details"') :]
    assert "https://llm.example/v1" in details


@pytest.mark.django_db
def test_a_bare_analysis_says_what_it_lacks(client, analysis_viewer):
    run = _analysis("pending", "nobody_knows")

    text = visible(client.get(_record(run)).content.decode())

    assert "AI analysis is still running" in text
    assert "A provider this hub doesn't have" in text
    assert "Not part of an incident." in text
    assert "None recorded" in text and "Not recorded" in text


@pytest.mark.django_db
@BOTH
def test_hostile_text_is_escaped(client, analysis_viewer, headers):
    run = _analysis(
        summary=MARKUP,
        provider_config={"x": MARKUP},
        recommendations=[{"title": MARKUP, "description": MARKUP}],
    )

    for url in (LIST, _record(run)):
        assert "<img" not in client.get(url, **headers).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_pages_are_clean(client, analysis_viewer, label, headers):
    run, _ = _busy()

    for url in (LIST, _record(run)):
        assert not FORBIDDEN[label].search(client.get(url, **headers).content.decode()), url


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, headers):
    client.force_login(superuser)
    run, _ = _busy()
    _analysis("failed")

    for url, root in ((LIST, '<div id="analyses"'), (_record(run), '<div id="analysis"')):
        body = client.get(url, **headers).content.decode()
        region = body if headers else body[body.index(root) : body.index("</main>")]
        focusable, ids = focus_audit(region)
        assert [tag for tag, element_id in focusable if not element_id] == [], url
        assert len(ids) == len(set(ids)), url
