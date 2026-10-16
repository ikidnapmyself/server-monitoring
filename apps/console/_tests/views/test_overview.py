import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import named_incident
from apps.console.queries import overview as overview_queries
from apps.console.views.overview import OverviewView

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_non_staff_is_refused(client):
    user = get_user_model().objects.create_user("visitor", password="x")
    client.force_login(user)

    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 403
    assert "Staff access required." in resp.content.decode()


def test_inactive_staff_is_refused_by_the_view_itself():
    request = RequestFactory().get("/console/")
    request.user = get_user_model()(username="gone", is_staff=True, is_active=False)

    resp = OverviewView.as_view()(request)

    assert resp.status_code == 403
    assert "Staff access required." in resp.content.decode()


@pytest.mark.django_db
def test_staff_gets_the_page(client, staff):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 200
    assert "<html" in resp.content.decode()


@pytest.mark.django_db
def test_root_redirects_to_the_console(client):
    resp = client.get("/")

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, staff):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 200
    assert "<html" not in resp.content.decode()
    assert resp.templates[0].name == "console/partials/_overview.html"
    assert "console/base.html" not in [t.name for t in resp.templates]
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["HTTP_HX_HISTORY_RESTORE_REQUEST", "HTTP_HX_BOOSTED"])
def test_htmx_request_that_swaps_the_body_gets_the_full_page(client, staff, header):
    resp = client.get(reverse("console:overview"), **HTMX, **{header: "true"})

    assert resp.status_code == 200
    assert "<html" in resp.content.decode()
    assert resp.templates[0].name == "console/overview.html"


@pytest.mark.django_db
def test_anonymous_htmx_request_is_told_to_do_a_full_redirect(client):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next=/console/"


def _next_after_login(client, current_url):
    resp = client.get(reverse("console:overview"), **HTMX, HTTP_HX_CURRENT_URL=current_url)

    assert resp.status_code == 204
    target = urlsplit(resp["HX-Redirect"])
    assert target.path == reverse("admin:login")
    return parse_qs(target.query)["next"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("current_url", "expected"),
    [
        ("http://testserver/console/incidents/?page=2&q=disk", "/console/incidents/?page=2&q=disk"),
        ("http://testserver/console/incidents/#row-4", "/console/incidents/"),
    ],
    ids=["path-and-query", "fragment-dropped"],
)
def test_anonymous_htmx_login_returns_to_the_page_the_operator_was_on(
    client, current_url, expected
):
    assert _next_after_login(client, current_url) == [expected]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "current_url",
    [
        "https://evil.example/x",
        "http://testserver//evil.example/x",
        "javascript:alert(1)",
        "",
        "http://testserver/admin/logout/",
        "http://testserver/consolex/",
        "http://testserver",
        "http://testserver/console/../admin/logout/",
        "http://testserver/console/%2e%2e/admin/logout/",
        "http://testserver/console/..\\admin/logout/",
        "http://testserver/console/\t../admin/",
    ],
    ids=[
        "foreign-host",
        "scheme-relative-path",
        "bad-scheme",
        "empty",
        "outside-the-console",
        "prefix-lookalike",
        "bare-origin",
        "dot-segment",
        "encoded-dot-segment",
        "backslash-dot-segment",
        "tab-before-dot-segment",
    ],
)
def test_anonymous_htmx_login_ignores_a_current_url_it_cannot_trust(client, current_url):
    assert _next_after_login(client, current_url) == ["/console/"]


@pytest.mark.django_db
def test_non_htmx_login_ignores_the_current_url_header(client):
    resp = client.get(
        reverse("console:overview"), HTTP_HX_CURRENT_URL="http://testserver/console/other/"
    )

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_empty_install_reads_as_calm(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "No open incidents." in body
    assert "Nothing needs you right now." in body
    assert "No nodes have reported yet." in body


@pytest.mark.django_db
def test_page_shows_incidents_nodes_and_the_sparkline(client, staff):
    incident = named_incident("Disk full on web-1", severity="critical")
    node = Node.objects.create(instance_id="web-1", hostname="web-1")

    body = client.get(reverse("console:overview")).content.decode()

    assert "1 open incident, 1 critical." in body
    assert "Disk full on web-1" in body
    assert reverse("console:incident", args=[incident.pk]) in body
    assert reverse("admin:alerts_node_change", args=[node.pk]) in body
    assert "c-tone-critical" in body
    assert "<svg" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("severities", "tone"),
    [((), "ok"), (("warning", "info"), "warning"), (("warning", "critical"), "critical")],
    ids=["none-open", "no-critical", "critical"],
)
def test_the_headline_flash_takes_the_tone_of_what_is_open(client, staff, severities, tone):
    for severity in severities:
        Incident.objects.create(title=severity, severity=severity)

    body = client.get(reverse("console:overview")).content.decode()

    assert re.findall(r'<div class="c-flash c-flash--headline c-tone-(\w+)">', body) == [tone]


@pytest.mark.django_db
def test_page_loads_only_first_party_assets_and_disables_htmx_eval(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "console/vendor/htmx.min.js" in body
    assert "console/console.js" in body
    assert "console/console.css" in body
    assert '"allowEval":false' in body
    assert '"refreshOnHistoryMiss":true' in body
    assert '"historyCacheSize":0' in body
    assert '"timeout":10000' in body
    assert not re.search(r'(?:src|href)="(?:https?:)?//', body)


@pytest.mark.django_db
def test_page_polls_itself_and_links_onward(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert f'hx-get="{reverse("console:overview")}"' in body
    assert 'hx-trigger="every 30s, console:refresh"' in body
    assert reverse("admin:index") in body
    assert reverse("admin:logout") in body


def _readiness(monkeypatch, detail):
    monkeypatch.setattr(
        overview_queries,
        "build_readiness",
        lambda: [{"key": "x", "label": "Inbox", "status": "warn", "detail": detail, "url": "/x/"}],
    )


@pytest.mark.django_db
def test_readiness_problems_are_listed(client, staff, monkeypatch):
    _readiness(monkeypatch, "3 stuck")

    body = client.get(reverse("console:overview")).content.decode()

    assert "Inbox" in body and "3 stuck" in body
    assert "System ready." not in body


@pytest.mark.django_db
def test_readiness_detail_is_escaped(client, staff, monkeypatch):
    _readiness(monkeypatch, "<b>web-1</b> stuck")

    body = client.get(reverse("console:overview")).content.decode()

    assert "&lt;b&gt;web-1&lt;/b&gt; stuck" in body
    assert "<b>web-1</b>" not in body


@pytest.mark.django_db
def test_page_carries_the_hooks_console_js_binds_to(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    polling = re.search(r"<div[^>]*\bhx-get=[^>]*>", body).group()
    assert re.search(r"\sdata-poll[\s>]", polling)
    banner = re.search(r'<p id="c-banner"[^>]*>', body).group()
    assert re.search(r"\shidden[\s>]", banner)
    assert 'data-refresh-text="Couldn\'t refresh. Showing the last data that loaded."' in banner
    assert (
        'data-action-text="That action may not have gone through. Reloading the current state."'
        in banner
    )


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_anonymous_non_get_logs_in_to_the_overview_not_to_a_405(client, method):
    url = reverse("console:overview")

    plain = getattr(client, method)(f"{url}?x=1")
    htmx = getattr(client, method)(f"{url}?x=1", **HTMX)

    assert plain.status_code == 302
    assert plain["Location"] == f"{reverse('admin:login')}?next={url}"
    assert htmx.status_code == 204
    assert htmx["HX-Redirect"] == f"{reverse('admin:login')}?next={url}"


@pytest.mark.django_db
def test_anonymous_head_keeps_its_own_path(client):
    url = reverse("console:overview")

    resp = client.head(f"{url}?x=1")

    assert resp["Location"] == f"{reverse('admin:login')}?next={url}%3Fx%3D1"


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_rendered_overview_is_clean(client, staff, monkeypatch, label, headers):
    monkeypatch.setattr(overview_queries, "INCIDENT_LIMIT", 1)
    _readiness(monkeypatch, "3 stuck")
    Incident.objects.create(title="Disk full on web-1", severity="critical")
    Incident.objects.create(title="Swap low on web-1", severity="warning")
    Node.objects.create(instance_id="web-1", hostname="web-1")

    body = client.get(reverse("console:overview"), **headers).content.decode()

    assert "<svg" in body and "c-label" in body and "1 more open incident" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
def test_a_silent_node_renders_as_quiet(client, staff, monkeypatch):
    monkeypatch.setattr(overview_queries, "local_instance_id", lambda: "this-hub")
    node = Node.objects.create(instance_id="web-1", hostname="web-1")
    Node.objects.filter(pk=node.pk).update(last_seen=timezone.now() - timedelta(days=3))

    body = client.get(reverse("console:overview")).content.decode()

    assert '<span class="c-label c-tone-muted">quiet</span>' in body
    assert "healthy" not in body


def _polled_region(body):
    return body[body.index('id="overview"') : body.index("</main>")]


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])
def test_every_link_in_the_polled_region_has_a_unique_id(client, staff, monkeypatch, headers):
    monkeypatch.setattr(overview_queries, "INCIDENT_LIMIT", 2)
    _readiness(monkeypatch, "3 stuck")
    incidents = [
        Incident.objects.create(title=f"incident {index}", severity="warning") for index in range(3)
    ]
    nodes = [Node.objects.create(instance_id=f"web-{index}") for index in range(2)]

    body = client.get(reverse("console:overview"), **headers).content.decode()
    region = body if headers else _polled_region(body)

    links = re.findall(r"<a\b[^>]*>", region)
    ids = re.findall(r'\sid="([^"]*)"', region)
    expected = (
        [f"incident-{incident.pk}" for incident in incidents[:2]]
        + ["incidents-more", "readiness-x"]
        + [f"node-{node.pk}" for node in nodes]
    )
    assert len(links) == len(expected)
    assert [re.search(r'\sid="([^"]*)"', link).group(1) for link in links] == expected
    assert len(ids) == len(set(ids))


MARKUP = "<img src=x onerror=alert(1)>"


@pytest.mark.django_db
def test_incident_title_is_escaped(client, staff):
    named_incident(f"disk {MARKUP}", severity="critical")

    body = client.get(reverse("console:overview")).content.decode()

    assert "disk &lt;img src=x onerror=alert(1)&gt;" in body
    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["hostname", "instance_id"])
def test_node_name_is_escaped(client, staff, field):
    Node.objects.create(**{"instance_id": "n1", "hostname": "", field: f"web {MARKUP}"})

    body = client.get(reverse("console:overview")).content.decode()

    assert "web &lt;img src=x onerror=alert(1)&gt;" in body
    assert "<img" not in body


@pytest.mark.django_db
def test_incidents_past_the_cap_link_to_the_incident_list(client, staff, monkeypatch):
    monkeypatch.setattr(overview_queries, "INCIDENT_LIMIT", 2)
    for index in range(5):
        Incident.objects.create(title=f"incident {index}", severity="warning")
    Incident.objects.create(title="done", severity="warning", status="resolved")

    body = client.get(reverse("console:overview")).content.decode()

    assert body.count("c-row__more") == 1
    link = re.search(
        r'<li class="c-box__row c-row__more"><a id="incidents-more" href="([^"]+)">([^<]+)</a></li>',
        body,
    )
    assert link.group(2) == "3 more open incidents"
    assert link.group(1) == reverse("console:incidents")


@pytest.mark.django_db
def test_one_incident_past_the_cap_reads_in_the_singular(client, staff, monkeypatch):
    monkeypatch.setattr(overview_queries, "INCIDENT_LIMIT", 1)
    Incident.objects.create(title="a", severity="warning")
    Incident.objects.create(title="b", severity="warning")

    body = client.get(reverse("console:overview")).content.decode()

    assert ">1 more open incident</a>" in body


@pytest.mark.django_db
def test_incidents_within_the_cap_show_no_more_row(client, staff):
    Incident.objects.create(title="only", severity="warning")

    body = client.get(reverse("console:overview")).content.decode()

    assert "c-row__more" not in body


@pytest.mark.django_db
def test_the_more_link_opens_a_list_of_only_open_incidents(client, staff):
    named_incident("still open", severity="warning")
    named_incident("acked", severity="warning", status="acknowledged")
    named_incident("long gone", severity="warning", status="closed")

    resp = client.get(overview_queries.build_overview().more_url)

    assert resp.status_code == 200
    assert resp.templates[0].name == "console/incidents.html"
    body = resp.content.decode()
    assert "still open" in body and "acked" in body
    assert "long gone" not in body


@pytest.mark.django_db
def test_a_quiet_day_shows_an_empty_state_instead_of_a_chart(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "No incidents opened in the last 24 hours." in body
    assert "c-bars__chart" not in body


@pytest.mark.django_db
def test_a_busy_day_shows_one_bar_per_hour_with_its_count(client, staff):
    Incident.objects.create(title="Disk full")

    body = client.get(reverse("console:overview")).content.decode()

    assert body.count('<rect class="c-bars__bar') == 24
    assert ": 1 opened</title>" in body
    assert "No incidents opened in the last 24 hours." not in body


@pytest.mark.django_db
def test_open_incident_rows_name_their_host(client, staff):
    incident = Incident.objects.create(title="Disk full", severity="critical")
    Alert.objects.create(
        fingerprint="f",
        name="disk",
        source="cluster",
        labels={"hostname": "db-7"},
        incident=incident,
        started_at=timezone.now(),
    )

    body = client.get(reverse("console:overview")).content.decode()

    assert re.search(r'<span class="c-host">.*?</svg>db-7</span>', body)
