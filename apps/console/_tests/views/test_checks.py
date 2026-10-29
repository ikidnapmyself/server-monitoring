import re

import pytest
from django.urls import reverse

from apps.console._tests.queries.test_checks import _check
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import focus_audit
from config.admin_badges import CHECK_STATUS_TONES

HTMX = {"HTTP_HX_REQUEST": "true"}
REPORTS_CURRENT = '<a class="c-unav__item" href="/console/reports/" aria-current="page">'
MARKUP = "<img src=x onerror=alert(1)>"
TONE_ICONS = {"ok": "check-circle", "critical": "x-circle", "warning": "alert", "muted": "skip"}
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


@pytest.fixture
def url():
    return reverse("console:checks")


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    found = re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    )
    return found.group(1) if found else None


def test_the_list_route(url):
    assert url == "/console/checks/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/checks/"


@pytest.mark.django_db
def test_a_superuser_reads_the_list(client, superuser, url):

    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/checks.html"
    assert "<title>Check runs · Console</title>" in body
    assert _nav(body, "Reports") == REPORTS_CURRENT
    assert f'<a class="c-subnav__item" href="{url}" aria-current="page">Checks</a>' in body
    assert "octicon-graph" in body


@pytest.mark.django_db
def test_other_pages_list_reports_without_marking_it(client, superuser, url):

    body = client.get(reverse("console:runs")).content.decode()

    assert _nav(body, "Reports") == '<a class="c-unav__item" href="/console/reports/">'


@pytest.mark.django_db
def test_the_partial_is_the_polled_root(client, superuser, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_checks.html"
    assert body.lstrip().startswith('<div id="checks"')
    root = _tag(body, "checks")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{url}"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_rows_link_to_the_check_run_with_its_meta(client, superuser, url):
    run = _check(status="critical", hostname="web", duration_ms=40)
    bare = _check(hostname="")

    body = client.get(url).content.decode()

    assert re.search(rf'<a id="check-{run.pk}" href="/console/checks/{run.pk}/">[^<]+</a>', body)
    assert re.search(r"CPU usage · <span class=\"c-host\">.*?</svg>web</span> · <time", body)
    assert "</time> · 40ms</div>" in body
    assert f'id="check-{bare.pk}"' in body
    assert "Unknown host</span>" in body
    assert "octicon-x-circle" in body


@pytest.mark.django_db
def test_the_form_carries_the_checker_and_host_and_resends_the_rest(client, superuser, url):
    _check(hostname="web")

    body = client.get(f"{url}?status=failing&since=7d&checker=cpu&host=web&q=abc").content.decode()

    form = body[body.index('<form class="c-search') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="status" value="failing">' in form
    assert '<input type="hidden" name="since" value="7d">' in form
    assert 'type="hidden" name="checker"' not in form
    assert 'value="abc"' in _tag(form, "checks-q")
    assert '<option value="cpu" selected>CPU usage</option>' in form
    assert '<option value="web" selected>web</option>' in form
    assert 'hx-target="#checks"' in form and 'hx-sync="#checks:replace"' in form


@pytest.mark.django_db
def test_the_status_chips_carry_the_icon_of_their_tone(client, superuser, url):
    run = _check("gone_checker", "unknown")

    body = client.get(f"{url}?status=failing").content.decode()

    header = body[body.index('<nav class="c-states"') :]
    header = header[: header.index("</nav>")]
    assert 'aria-current="true"' in _tag(header, "chip-status-failing")

    def icons(html):
        return re.findall(r"#octicon-([a-z-]+)", html)

    def chip(key):
        found = header[header.index(f'id="chip-status-{key}"') :]
        return icons(found[: found.index("</a>")])

    for key, tone in {"failing": "critical", **CHECK_STATUS_TONES}.items():
        assert chip(key) == [TONE_ICONS[tone]], key
    assert chip("all") == []
    row = body[body.index(f'id="check-{run.pk}"') - 400 : body.index(f'id="check-{run.pk}"')]
    assert icons(row)[-1] == chip("unknown")[0] == "skip"
    assert 'id="chip-since-24h"' in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [
        (False, "", "No check runs yet."),
        (True, "?status=critical", "No check runs match these filters."),
    ],
)
def test_each_blank_state(client, superuser, url, setup, query, heading):
    if setup:
        _check()

    body = client.get(f"{url}{query}").content.decode()

    assert heading in body
    if setup:
        clear = _tag(body, "filters-clear")
        assert f'href="{url}"' in clear and 'hx-target="#checks"' in clear


@pytest.mark.django_db
def test_an_invalid_filter_is_named(client, superuser, url):
    body = client.get(f"{url}?status=bogus&checker=nope").content.decode()

    assert "Ignored invalid filter: status, checker." in body


@pytest.mark.django_db
def test_more_than_a_page_offers_the_pager(client, superuser, url, monkeypatch):
    monkeypatch.setattr("apps.console.queries.checks.PAGE_SIZE", 1)
    _check()
    _check()

    first = client.get(url).content.decode()
    second = client.get(f"{url}?page=2").content.decode()

    assert 'id="page-next"' in first and 'id="page-prev"' not in first
    assert 'id="page-prev"' in second and 'id="page-next"' not in second
    assert "Page 2 of 2" in second


@pytest.mark.django_db
@BOTH
def test_hostile_text_is_escaped(client, superuser, url, headers):
    _check("gone_checker", message=MARKUP, hostname=MARKUP)

    body = client.get(url, **headers).content.decode()

    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_list_is_clean(client, superuser, url, label, headers):
    _check(status="critical")

    body = client.get(url, **headers).content.decode()

    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, url, headers):
    _check(status="critical")
    _check()

    body = client.get(url, **headers).content.decode()
    region = body if headers else body[body.index('<div id="checks"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
