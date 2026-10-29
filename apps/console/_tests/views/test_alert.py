import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries import alert as alert_query

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


def _url(alert):
    return reverse("console:alert", args=[alert.pk])


def _checker_alert(**fields):
    fields = {
        "fingerprint": "check:i-web:cpu",
        "name": "cpu",
        "severity": "critical",
        "status": "firing",
        "source": "cluster",
        "labels": {"checker": "cpu", "instance_id": "i-web"},
        "raw_payload": {"metrics": {"cpu_percent": 97.1}},
        "trace_id": "t-1",
        "started_at": timezone.now(),
        **fields,
    }
    return Alert.objects.create(**fields)


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def test_the_page_and_reevaluate_routes():
    assert reverse("console:alert", args=[7]) == "/console/alerts/7/"
    assert reverse("console:alert-reeval", args=[7]) == "/console/alerts/7/reevaluate/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    alert = _checker_alert()

    resp = client.get(_url(alert))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next={_url(alert)}"


@pytest.mark.django_db
@BOTH
def test_a_missing_alert_is_a_console_404(client, superuser, headers):
    resp = client.get(reverse("console:alert", args=[999]), **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert resp.templates[0].name == "console/not_found.html"
    assert "<html" in body
    assert 'aria-current="page"' in _nav(body, "Alerts")


@pytest.mark.django_db
def test_view_alert_reads_the_page_with_the_nav_tab_current(client, superuser):
    alert = _checker_alert()

    resp = client.get(_url(alert))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/alert.html"
    assert _nav(body, "Alerts") == (
        f'<a class="c-unav__item" href="{reverse("console:alerts")}" aria-current="page">'
    )
    assert 'id="alert-back"' in body and "Back to alerts" in body
    assert re.search(r'<div id="alert" data-poll hx-get="/console/alerts/\d+/"', body)


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, superuser):
    resp = client.get(_url(_checker_alert()), **HTMX)

    body = resp.content.decode()
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_alert.html"


@pytest.mark.django_db
@BOTH
def test_the_incident_card_links_the_incident(client, superuser, headers):
    incident = Incident.objects.create(severity="critical")
    alert = _checker_alert(incident=incident)

    body = client.get(_url(alert), **headers).content.decode()

    assert f'<a id="alert-incident" href="/console/incidents/{incident.pk}/">' in body
    assert "Not part of an incident." not in body


@pytest.mark.django_db
def test_an_alert_outside_an_incident_says_so(client, superuser):
    body = client.get(_url(_checker_alert())).content.decode()

    assert "Not part of an incident." in body
    assert 'id="alert-incident"' not in body


@pytest.mark.django_db
def test_a_superuser_gets_the_reevaluate_button_on_a_checker_alert(client, superuser):
    alert = _checker_alert()

    body = client.get(_url(alert)).content.decode()

    assert (
        f'<a id="action-reevaluate_alert" class="c-button c-button--sm c-button--primary" '
        f'href="/console/alerts/{alert.pk}/reevaluate/">'
    ) in body
    assert "Do this" in body


@pytest.mark.django_db
def test_details_hold_the_fingerprint_and_raw_json(client, superuser):
    body = client.get(_url(_checker_alert())).content.decode()

    details = body[body.index('<details id="alert-details"') :]
    assert "<dt>Fingerprint</dt><dd>check:i-web:cpu</dd>" in details
    assert "<dt>Trace ID</dt><dd>t-1</dd>" in details
    assert "&quot;cpu_percent&quot;: 97.1" in details


@pytest.mark.django_db
def test_the_sidebar_names_severity_status_node_source_and_end(client, superuser):
    node = Node.objects.create(instance_id="i-web", hostname="web")
    alert = _checker_alert(node=node, status="resolved", ended_at=timezone.now())

    body = client.get(_url(alert)).content.decode()

    sidebar = body[body.index('<aside class="c-sidebar"') :]
    for heading in ("Severity", "Status", "Node", "Source", "Ended"):
        assert f'<h2 class="c-sidebar__heading">{heading}</h2>' in sidebar
    assert "<span>web</span>" in sidebar
    assert "c-status--resolved" in body


@pytest.mark.django_db
def test_a_firing_alert_has_no_end_in_the_sidebar(client, superuser):
    body = client.get(_url(_checker_alert())).content.decode()

    assert '<h2 class="c-sidebar__heading">Ended</h2>' not in body
    assert "c-state c-status--firing" in body


@pytest.mark.django_db
def test_history_is_listed_and_capped(client, superuser, monkeypatch):
    monkeypatch.setattr(alert_query, "HISTORY_LIMIT", 1)
    alert = _checker_alert()
    AlertHistory.objects.create(alert=alert, event="created", new_status="firing")
    AlertHistory.objects.create(alert=alert, event="resolved", new_status="resolved")

    body = client.get(_url(alert)).content.decode()

    assert '<ol class="c-timeline"' in body
    assert "cleared" in body
    assert "1 earlier entry not shown." in body


@pytest.mark.django_db
def test_an_alert_with_no_history_shows_no_note(client, superuser):
    body = client.get(_url(_checker_alert())).content.decode()

    assert "not shown." not in body
    assert "Never re-evaluated." in body


@pytest.mark.django_db
def test_reevaluation_records_are_listed(client, superuser):
    alert = _checker_alert(annotations={"severity_reevaluated": "not json"})

    body = client.get(_url(alert)).content.decode()

    assert "Never re-evaluated." not in body
    assert re.search(r'<dl class="c-facts">\s*<div><dt>', body[body.index("Re-evaluation") :])


@pytest.mark.django_db
@BOTH
def test_a_hostile_webhook_alert_is_escaped(client, superuser, headers):
    alert = _checker_alert(name=MARKUP, labels={}, source="grafana", description=MARKUP)

    body = client.get(_url(alert), **headers).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_rendered_page_is_clean(client, superuser, label, headers):
    alert = _checker_alert(incident=Incident.objects.create())
    AlertHistory.objects.create(alert=alert, event="created", new_status="firing")

    body = client.get(_url(alert), **headers).content.decode()

    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(client, superuser, headers):
    alert = _checker_alert(incident=Incident.objects.create())

    body = client.get(_url(alert), **headers).content.decode()
    region = body if headers else body[body.index('<div id="alert"') : body.index("</main>")]

    tags = re.findall(r"<(?:a|button|summary)\b[^>]*>", region)
    ids = [re.search(r'\sid="([^"]*)"', tag).group(1) for tag in tags]
    assert len(ids) == len(set(ids)) == len(tags)
