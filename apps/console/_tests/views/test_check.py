import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.console._tests.queries.test_checks import _check
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_checks import REPORTS_CURRENT, _nav
from apps.console._tests.views.test_incident import focus_audit
from apps.orchestration.models import PipelineRun

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


def _url(run):
    return reverse("console:check", args=[run.pk])


def _busy():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    alert = Alert.objects.create(
        fingerprint="check:i-web:cpu",
        name="cpu",
        severity="critical",
        source="cluster",
        started_at=timezone.now(),
    )
    run = _check(
        status="critical",
        hostname="web",
        alert=alert,
        trace_id="0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11",
        error="sensor_read failed",
        metrics={"cpu_percent": 97.0},
        warning_threshold=80,
        critical_threshold=90,
    )
    PipelineRun.objects.create(run_id="r-1", trace_id=run.trace_id)
    return run, node, alert


def test_the_page_route():
    assert reverse("console:check", args=[7]) == "/console/checks/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_console_login(client):
    run = _check()

    resp = client.get(_url(run))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(run)}"


@pytest.mark.django_db
def test_a_superuser_reads_the_page(client, superuser):
    run, _, _ = _busy()

    resp = client.get(_url(run))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/check.html"
    assert re.search(r"<title>[^<]+ · Console</title>", body)
    assert _nav(body, "Reports") == REPORTS_CURRENT
    assert 'id="check-back"' in body and "Back to check runs" in body


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, superuser):
    run, _, _ = _busy()

    resp = client.get(_url(run), **HTMX)

    body = resp.content.decode()
    assert resp.templates[0].name == "console/partials/_check.html"
    assert "<html" not in body and 'id="check"' not in body


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_check_run_is_the_console_404_page(client, superuser, pk, headers):
    resp = client.get(f"/console/checks/{pk}/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert "This check run doesn't exist." in body
    assert f'<a id="not-found-back" href="{reverse("console:checks")}">Back to check runs</a>' in (
        body
    )


@pytest.mark.django_db
def test_a_full_reader_gets_every_link(client, superuser):
    run, node, alert = _busy()

    body = client.get(_url(run)).content.decode()

    assert f'<a id="check-node" href="/console/nodes/{node.pk}/">web</a>' in body
    assert f'<a id="check-alert" href="/console/alerts/{alert.pk}/">Alert #{alert.pk}</a>' in body
    assert (
        '<a id="check-run" href="/console/runs/?q=0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11">'
        "Part of a run</a>"
    ) in body
    text = visible(body)
    assert "Warns at 80, critical at 90" in text
    assert "CPU usage" in text
    assert "sensor_read" not in text and "0b9e6c1e" not in text
    details = body[body.index('id="check-what-details"') :]
    assert "sensor_read failed" in details
    assert "&quot;cpu_percent&quot;: 97.0" in details


@pytest.mark.django_db
def test_a_bare_run_says_what_it_lacks(client, superuser):
    run = _check("gone_checker", hostname="")

    text = visible(client.get(_url(run)).content.decode())

    assert "Unknown host" in text
    assert "None set" in text
    assert "Not part of a run" in text
    assert "Gone checker" in text


@pytest.mark.django_db
def test_a_host_without_one_node_reads_as_text(client, superuser):
    run = _check(hostname="db")

    body = client.get(_url(run)).content.decode()

    assert 'id="check-node"' not in body
    assert "<span>db</span>" in body


@pytest.mark.django_db
@BOTH
def test_hostile_text_is_escaped(client, superuser, headers):
    run = _check("gone_checker", message=MARKUP, hostname=MARKUP, error=MARKUP)

    body = client.get(_url(run), **headers).content.decode()

    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_page_is_clean(client, superuser, label, headers):
    run, _, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()

    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id(client, superuser, headers):
    run, _, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()
    region = body if headers else body[body.index('<div id="check"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
