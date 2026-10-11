import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

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
    assert [t.name for t in resp.templates] == ["console/partials/_overview.html"]
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
    from apps.alerts.models import Incident, Node

    incident = Incident.objects.create(title="Disk full on web-1", severity="critical")
    node = Node.objects.create(instance_id="web-1", hostname="web-1")

    body = client.get(reverse("console:overview")).content.decode()

    assert "1 open incident, 1 critical." in body
    assert "Disk full on web-1" in body
    assert reverse("admin:alerts_incident_change", args=[incident.pk]) in body
    assert reverse("admin:alerts_node_change", args=[node.pk]) in body
    assert "c-tone-critical" in body
    assert "<svg" in body


@pytest.mark.django_db
def test_page_loads_only_first_party_assets_and_disables_htmx_eval(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "console/vendor/htmx.min.js" in body
    assert "console/console.js" in body
    assert "console/console.css" in body
    assert '"allowEval":false' in body
    assert '"refreshOnHistoryMiss":true' in body
    assert not re.search(r'(?:src|href)="(?:https?:)?//', body)


@pytest.mark.django_db
def test_page_polls_itself_and_links_onward(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert f'hx-get="{reverse("console:overview")}"' in body
    assert 'hx-trigger="every 30s"' in body
    assert reverse("admin:index") in body
    assert reverse("admin:logout") in body


def _readiness(monkeypatch, detail):
    from apps.console.queries import overview

    monkeypatch.setattr(
        overview,
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
