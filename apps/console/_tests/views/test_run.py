import re

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Node
from apps.console._tests.fixtures import CHECK_SNAPSHOT
from apps.console._tests.queries.test_runs import _incident, _run
from apps.console._tests.test_guard import visible
from apps.console._tests.test_templates import FORBIDDEN
from apps.console._tests.views.test_incident import focus_audit
from apps.orchestration.models import StageExecution

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


def _url(run):
    return reverse("console:run", args=[run.pk])


def _nav(body, label):
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def _busy(status="failed"):
    node = Node.objects.create(instance_id="i-web", hostname="web")
    incident = _incident()
    run = _run(
        status=status,
        incident=incident,
        node=node,
        origin="manual",
        current_stage="notify",
        total_attempts=2,
        last_error_message="notify: slack 500",
        inbound_payload={"downstream_incident_id": incident.pk},
    )
    check = StageExecution.objects.create(
        pipeline_run=run,
        stage="check",
        attempt=1,
        status="succeeded",
        started_at=timezone.now(),
        output_snapshot=CHECK_SNAPSHOT,
    )
    return run, incident, check


def test_the_page_route():
    assert reverse("console:run", args=[7]) == "/console/runs/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_console_login(client):
    run = _run()

    resp = client.get(_url(run))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('console:login')}?next={_url(run)}"


@pytest.mark.django_db
def test_a_superuser_reads_the_page(client, superuser):
    run, _, _ = _busy()

    resp = client.get(_url(run))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/run.html"
    assert "<title>Run failed · Console</title>" in body
    assert _nav(body, "Runs") == (
        f'<a class="c-unav__item" href="{reverse("console:runs")}" aria-current="page">'
    )


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, superuser):
    run, _, _ = _busy()

    resp = client.get(_url(run), **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/partials/_run.html"
    assert "<html" not in body and 'id="run"' not in body


@pytest.mark.django_db
def test_the_page_polls_its_wrapper(client, superuser):
    run, _, _ = _busy()

    body = client.get(_url(run)).content.decode()

    wrapper = re.search(r'<div id="run"[^>]*>', body).group()
    assert re.search(r"\sdata-poll[\s>]", wrapper)
    assert f'hx-get="{_url(run)}"' in wrapper
    assert 'hx-trigger="every 30s, console:refresh"' in wrapper
    assert 'hx-swap="innerHTML"' in wrapper


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_run_is_the_console_404_page(client, superuser, pk, headers):
    resp = client.get(f"/console/runs/{pk}/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "console/not_found.html"
    assert "This run doesn't exist. It may have been deleted." in body
    assert '<a id="not-found-back" href="/console/runs/">Back to runs</a>' in body
    assert 'aria-current="page"' in _nav(body, "Runs")


@pytest.mark.django_db
def test_the_header_and_sidebar_describe_the_run(client, superuser):
    run, _, _ = _busy()

    body = client.get(_url(run)).content.decode()

    assert re.search(
        rf'<h1 class="c-pagehead__title">Run failed <span class="c-pagehead__number">#{run.pk}',
        body,
    )
    assert '<a id="run-back" class="c-crumb" href="/console/runs/">' in body
    sidebar = body[body.index('<aside class="c-sidebar"') :]
    for heading, value in (
        ("Node", "web"),
        ("Origin", "Manual / CLI"),
        ("Stage reached", "Notify"),
        ("Attempts", "2"),
    ):
        assert re.search(rf"{heading}</h2>.*?<span>{re.escape(value)}</span>", sidebar, re.S)


@pytest.mark.django_db
def test_a_bare_run_says_what_it_lacks(client, superuser):
    run = _run()

    body = client.get(_url(run)).content.decode()

    sidebar = body[body.index('<aside class="c-sidebar"') :]
    assert re.search(r"Node</h2>.*?Unknown host", sidebar, re.S)
    assert re.search(r"Stage reached</h2>.*?None", sidebar, re.S)
    assert "Not part of an incident." in body
    assert 'id="run-incident"' not in body


@pytest.mark.django_db
@BOTH
def test_an_operator_sees_retry_on_a_failed_run(client, superuser, headers):
    run, _, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()

    assert "Do this" in body
    assert (
        '<button id="action-retry_run" class="c-button c-button--sm c-button--primary" '
        'type="submit">Retry</button>'
    ) in body
    assert "Mark failed" not in body


@pytest.mark.django_db
def test_an_operator_sees_mark_failed_on_a_queued_run(client, superuser):
    run = _run(status="pending")

    body = client.get(_url(run)).content.decode()

    assert (
        '<button id="action-abandon_run" class="c-button c-button--sm c-button--danger" '
        'type="submit">Mark failed</button>'
    ) in body


@pytest.mark.django_db
@BOTH
def test_the_incident_card_links_the_console_incident(client, superuser, headers):
    run, incident, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()

    assert f'<a id="run-incident" href="/console/incidents/{incident.pk}/">' in body
    assert f"#{incident.pk} · Open" in body


@pytest.mark.django_db
def test_stage_output_renders_through_the_shared_body(client, superuser):
    run, _, check = _busy()

    body = client.get(_url(run)).content.decode()

    assert f'<details id="stage-{check.pk}">' in body
    assert f'id="stage-{check.pk}-problem-1' in body
    assert "CPU usage" in body
    assert f'id="stage-{check.pk}-what-details"' in body


@pytest.mark.django_db
def test_the_payload_and_ids_render_only_in_details(client, superuser):
    run, incident, _ = _busy()

    body = client.get(_url(run)).content.decode()

    assert run.run_id in body and run.trace_id in body
    assert 'id="run-what-details-json"' in body
    assert "downstream_incident_id" in body
    text = visible(body)
    assert run.run_id not in text and run.trace_id not in text
    assert "downstream_incident_id" not in text and "{" not in text


@pytest.mark.django_db
@BOTH
def test_markup_in_an_error_is_escaped(client, superuser, headers):
    run = _run(status="failed", last_error_message=MARKUP, last_error_type=MARKUP)
    StageExecution.objects.create(
        pipeline_run=run, stage="notify", attempt=1, status="failed", error_message=MARKUP
    )

    body = client.get(_url(run), **headers).content.decode()

    assert ESCAPED in body
    assert "<img" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_no_inline_script_or_handler(client, superuser, label, headers):
    run, _, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()

    assert "c-timeline" in body and "c-raw" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(client, superuser, headers):
    run, _, _ = _busy()

    body = client.get(_url(run), **headers).content.decode()
    region = body if headers else body[body.index('<div id="run"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
    assert "action-retry_run" in ids
