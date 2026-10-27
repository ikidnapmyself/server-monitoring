import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries import alerts as alert_queries

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


@pytest.fixture
def url():
    return reverse("console:alerts")


def _alert(key, **fields):
    fields = {
        "fingerprint": f"fp-{key}",
        "severity": "warning",
        "status": "firing",
        "source": "cluster",
        "started_at": timezone.now(),
        **fields,
    }
    return Alert.objects.create(name=key, **fields)


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def test_the_list_lives_at_alerts(url):
    assert url == "/console/alerts/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client, url):
    resp = client.get(url)

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/alerts/"


@pytest.mark.django_db
def test_view_alert_reads_the_full_page_with_the_nav_tab_current(client, superuser, url):
    resp = client.get(url)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/alerts.html"
    assert "<title>Alerts · Console</title>" in body
    assert _nav(body, "Alerts") == f'<a class="c-unav__item" href="{url}" aria-current="page">'
    assert _nav(body, "Incidents") == (
        f'<a class="c-unav__item" href="{reverse("console:incidents")}">'
    )


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial_rooted_at_the_polled_element(client, superuser, url):
    resp = client.get(url, **HTMX)

    body = resp.content.decode()
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_alerts.html"
    assert body.lstrip().startswith('<div id="alerts"')
    root = _tag(body, "alerts")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{url}"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="outerHTML"' in root


@pytest.mark.django_db
def test_a_row_links_the_alert_page_and_its_incident(client, superuser, url):
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = Incident.objects.create(severity="warning")
    alert = _alert("Disk full", node=node, incident=incident)
    lone = _alert("Swap low", source="grafana")

    body = client.get(url).content.decode()

    assert f'<a id="alert-{alert.pk}" href="/console/alerts/{alert.pk}/">' in body
    assert (
        f'<a id="alert-{alert.pk}-incident" href="/console/incidents/{incident.pk}/">'
        f"Incident #{incident.pk}</a>"
    ) in body
    assert f"alert-{lone.pk}-incident" not in body
    assert re.search(r'class="c-box__row [^"]*c-status--firing"', body)
    assert '<span class="c-label c-tone-warning">warning</span>' in body
    assert re.search(r'<span class="c-host">.*?</svg>web</span> · cluster · seen <time', body)
    assert re.search(r'<span class="c-host c-host--unknown">.*?</svg>Unknown host</span>', body)
    assert "octicon-alert" in body


@pytest.mark.django_db
@BOTH
def test_a_hostile_webhook_alert_name_is_escaped(client, superuser, url, headers):
    _alert(MARKUP, source="grafana")

    body = client.get(url, **headers).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@pytest.mark.django_db
def test_the_search_form_resends_the_kept_filters(client, superuser, url):
    _alert("a", severity="critical")

    body = client.get(f"{url}?severity=critical&status=all&q=fp").content.decode()

    form = body[body.index('<form class="c-search"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="status" value="all">' in form
    assert '<input type="hidden" name="severity" value="critical">' in form
    assert form.count('type="hidden"') == 2
    assert 'value="fp"' in _tag(form, "alerts-q")
    assert 'hx-target="#alerts"' in form
    assert 'hx-sync="#alerts:replace"' in form


@pytest.mark.django_db
def test_a_source_chip_id_is_a_counter_never_the_source_name(client, superuser, url):
    _alert("a", source="grafana")
    _alert("b", source=f"bad {MARKUP}")

    body = client.get(url).content.decode()

    sources = re.findall(r'id="chip-source-([^"]*)"', body)
    assert sources == ["1", "2", "3"]
    assert 'aria-current="true"' in _tag(body, "chip-source-1")
    assert 'id="chip-severity-critical"' in body


@pytest.mark.django_db
def test_the_status_chips_carry_their_icons(client, superuser, url):
    body = client.get(url).content.decode()

    header = body[body.index('<nav class="c-states"') :]
    header = header[: header.index("</nav>")]
    firing = header[header.index('id="chip-status-firing"') : header.index("chip-status-resolved")]
    resolved = header[header.index('id="chip-status-resolved"') : header.index("chip-status-all")]
    every = header[header.index('id="chip-status-all"') :]
    assert "octicon-alert" in firing
    assert "octicon-check-circle" in resolved
    assert "octicon" not in every


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "query", "heading"),
    [
        (None, "", "No alerts yet."),
        ("resolved", "", "Nothing is firing."),
        ("firing", "?severity=info", "No alerts match these filters."),
    ],
)
def test_each_blank_state(client, superuser, url, setup, query, heading):
    if setup:
        _alert("a", status=setup)

    body = client.get(f"{url}{query}").content.decode()

    assert heading in body
    if heading.startswith("No alerts match"):
        clear = _tag(body, "filters-clear")
        assert f'href="{url}"' in clear and 'hx-target="#alerts"' in clear
    else:
        assert "filters-clear" not in body


@pytest.mark.django_db
def test_the_pager_swaps_the_list(client, superuser, url, monkeypatch):
    monkeypatch.setattr(alert_queries, "PAGE_SIZE", 1)
    for key in "abc":
        _alert(key)

    body = client.get(f"{url}?page=2").content.decode()

    for link in (_tag(body, "page-prev"), _tag(body, "page-next")):
        assert 'hx-target="#alerts"' in link
        assert 'hx-sync="#alerts:replace"' in link
    assert "Page 2 of 3" in body


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(
    client, superuser, url, monkeypatch, headers
):
    monkeypatch.setattr(alert_queries, "PAGE_SIZE", 1)
    node = Node.objects.create(instance_id="i-web", hostname="web")
    _alert("a", node=node, incident=Incident.objects.create(), source="grafana")
    _alert("b")

    body = client.get(url, **headers).content.decode()
    region = body if headers else body[body.index('<div id="alerts"') : body.index("</main>")]

    tags = re.findall(r"<(?:a|button|input(?![^>]*hidden))\b[^>]*>", region)
    ids = [re.search(r'\sid="([^"]*)"', tag).group(1) for tag in tags]
    assert len(ids) == len(set(ids)) == len(tags)


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_rendered_list_is_clean(client, superuser, url, label, headers):
    _alert("a", source="grafana")
    _alert("b")

    body = client.get(f"{url}?since=bogus", **headers).content.decode()

    assert '<div class="c-flash' in body
    assert not FORBIDDEN[label].search(body), label
