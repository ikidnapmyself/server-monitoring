import re

import pytest
from django.urls import reverse

from apps.alerts.models import Node
from apps.console._tests.queries.test_runs import _incident, _run
from apps.console._tests.test_templates import FORBIDDEN

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


@pytest.fixture
def url():
    return reverse("console:runs")


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def test_the_list_route(url):
    assert url == "/console/runs/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_console_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next=/console/runs/"


@pytest.mark.django_db
def test_a_superuser_reads_the_list(client, superuser, url):
    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/runs.html"
    assert "<title>Runs · Console</title>" in body


@pytest.mark.django_db
def test_the_partial_is_the_polled_root(client, superuser, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_runs.html"
    assert body.lstrip().startswith('<div id="runs"')
    root = _tag(body, "runs")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{url}"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_rows_link_to_the_run_and_its_incident(client, superuser, url):
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = _incident()
    run = _run(
        status="failed",
        incident=incident,
        node=node,
        origin="manual",
        current_stage="notify",
        total_attempts=2,
    )
    lone = _run(total_attempts=1)

    body = client.get(url).content.decode()

    assert f'<a id="run-{run.pk}" href="/console/runs/{run.pk}/">Run failed</a>' in body
    assert (
        f'<a id="run-{run.pk}-incident" href="/console/incidents/{incident.pk}/">'
        f"Incident #{incident.pk}</a>"
    ) in body
    assert f"run-{lone.pk}-incident" not in body
    assert re.search(
        r'<span class="c-host">.*?</svg>web</span> · Manual / CLI · Notify · 2 attempts · <time',
        body,
    )
    assert re.search(r"Unknown host</span> · [^·]+ · 1 attempt · <time", body)


@pytest.mark.django_db
def test_the_search_form_resends_the_kept_filters(client, superuser, url):
    body = client.get(f"{url}?status=failed&origin=manual&q=abc").content.decode()

    form = body[body.index('<form class="c-search"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="status" value="failed">' in form
    assert '<input type="hidden" name="origin" value="manual">' in form
    assert 'value="abc"' in _tag(form, "runs-q")
    assert 'placeholder="Run ID, trace ID or fingerprint"' in form
    assert 'hx-target="#runs"' in form
    assert 'hx-sync="#runs:replace"' in form


@pytest.mark.django_db
def test_the_status_chips_carry_their_icons(client, superuser, url):
    body = client.get(url).content.decode()

    header = body[body.index('<nav class="c-states"') :]
    header = header[: header.index("</nav>")]
    failed = header[header.index('id="chip-status-failed"') : header.index("chip-status-active")]
    active = header[header.index('id="chip-status-active"') : header.index("chip-status-finished")]
    finished = header[header.index('id="chip-status-finished"') : header.index("chip-status-all")]
    every = header[header.index('id="chip-status-all"') :]
    assert "octicon-x-circle" in failed
    assert "octicon-sync" in active
    assert "octicon-check-circle" in finished
    assert "octicon" not in every
    assert 'aria-current="true"' in _tag(header, "chip-status-all")
    assert 'id="chip-origin-manual"' in body
    assert "chip-source" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [(False, "", "No runs yet."), (True, "?status=failed", "No runs match these filters.")],
)
def test_each_blank_state(client, superuser, url, setup, query, heading):
    if setup:
        _run()

    body = client.get(f"{url}{query}").content.decode()

    assert heading in body
    if setup:
        clear = _tag(body, "filters-clear")
        assert f'href="{url}"' in clear and 'hx-target="#runs"' in clear
    else:
        assert "Runs appear here when an incident changes." in body


@pytest.mark.django_db
def test_an_invalid_filter_is_named(client, superuser, url):
    body = client.get(f"{url}?status=bogus").content.decode()

    assert "Ignored invalid filter: status." in body


@pytest.mark.django_db
def test_more_than_a_page_offers_the_pager(client, superuser, url, monkeypatch):
    monkeypatch.setattr("apps.console.queries.runs.PAGE_SIZE", 1)
    _run()
    _run()

    first = client.get(url).content.decode()
    second = client.get(f"{url}?page=2").content.decode()

    assert 'id="page-next"' in first and 'id="page-prev"' not in first
    assert 'id="page-prev"' in second and 'id="page-next"' not in second
    assert "Page 2 of 2" in second


@pytest.mark.django_db
def test_the_nav_marks_runs_current(client, superuser, url):
    body = client.get(url).content.decode()

    assert _nav(body, "Runs") == f'<a class="c-unav__item" href="{url}" aria-current="page">'
    assert _nav(body, "Alerts") == f'<a class="c-unav__item" href="{reverse("console:alerts")}">'


@pytest.mark.django_db
def test_other_pages_list_runs_without_marking_it(client, superuser, url):

    body = client.get(reverse("console:alerts")).content.decode()

    assert _nav(body, "Runs") == f'<a class="c-unav__item" href="{url}">'


@pytest.mark.django_db
@BOTH
def test_a_hostile_error_is_escaped(client, superuser, url, headers):
    _run(status="failed", last_error_message=MARKUP)

    body = client.get(url, **headers).content.decode()

    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_list_is_clean(client, superuser, url, label, headers):
    _run(status="failed", incident=_incident())

    body = client.get(url, **headers).content.decode()

    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, url, headers):
    _run(status="failed", incident=_incident())
    _run()

    body = client.get(url, **headers).content.decode()
    region = body if headers else body[body.index('<div id="runs"') : body.index("</main>")]

    tags = re.findall(r"<(?:a|button|summary|input)\b(?![^>]*type=\"hidden\")[^>]*>", region)
    ids = [re.search(r'\sid="([^"]*)"', tag).group(1) for tag in tags]
    assert len(ids) == len(set(ids)) == len(tags)


@pytest.mark.django_db
def test_a_reason_filter_is_shown_and_removable(client, superuser, url):
    body = client.get(f"{url}?status=failed&reason=no_route").content.decode()

    toolbar = body[body.index('<div class="c-toolbar">') :]
    toolbar = toolbar[: toolbar.index('<div class="c-box">')]
    current = _tag(toolbar, "chip-reason-no_route")
    assert 'aria-current="true"' in current
    assert ">No route matches this incident</a>" in toolbar
    remove = _tag(toolbar, "chip-reason-any")
    assert 'href="/console/runs/?status=failed"' in remove
    assert 'hx-target="#runs"' in remove
    assert "chip-reason" not in client.get(url).content.decode()
