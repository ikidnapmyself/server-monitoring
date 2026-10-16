import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console._tests.queries.test_incident import (
    ANALYZE_SNAPSHOT,
    CHECK_SNAPSHOT,
    NOTIFY_SNAPSHOT,
)
from apps.console._tests.test_templates import FORBIDDEN
from apps.console.queries import incident as incident_queries
from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution

HTMX = {"HTTP_HX_REQUEST": "true"}
MARKUP = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BOTH = pytest.mark.parametrize("headers", [{}, HTMX], ids=["page", "partial"])


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


def _url(incident):
    return reverse("console:incident", args=[incident.pk])


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    """The opening tag of the underline nav tab named ``label``."""
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


def named_incident(name, **fields):
    """An incident whose one webhook alert gives it ``name`` as its described headline."""
    incident = Incident.objects.create(title=name, **fields)
    Alert.objects.create(
        fingerprint=f"named:{incident.pk}",
        name=name,
        severity=incident.severity,
        source="grafana",
        incident=incident,
        started_at=timezone.now(),
    )
    return incident


def _busy(suffix=""):
    """An acknowledged incident with one alert, two history rows and a full run."""
    pipeline = PipelineDefinition.objects.create(
        name=f"pager{suffix}", stages=["check", "analyze", "notify"]
    )
    incident = Incident.objects.create(
        title=f"Disk full{suffix}",
        severity="critical",
        status="acknowledged",
        description="stale description",
        instance=f"web-1{suffix}",
        summary=f"Logs rotated{suffix}",
        acknowledged_at=timezone.now(),
        pipeline=pipeline,
    )
    node = Node.objects.create(instance_id="web-1", hostname=f"web-1{suffix}")
    alert = Alert.objects.create(
        fingerprint="check:web-1:disk",
        name=f"disk{suffix}",
        description=f"92% used{suffix}",
        severity="critical",
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )
    history = [
        AlertHistory.objects.create(alert=alert, event="created", new_status="firing"),
        AlertHistory.objects.create(alert=alert, event="refired"),
    ]
    run = PipelineRun.objects.create(
        trace_id="trace-1",
        run_id="run-1",
        incident=incident,
        status="notified",
        inbound_payload={"downstream_incident_id": incident.pk},
    )
    now = timezone.now()
    stages = {
        name: StageExecution.objects.create(
            pipeline_run=run,
            stage=name,
            status="succeeded",
            started_at=now + timedelta(seconds=index),
            duration_ms=850.0,
            output_snapshot=snapshot,
        )
        for index, (name, snapshot) in enumerate(
            [
                ("check", {**CHECK_SNAPSHOT, "errors": [f"disk timed out{suffix}"]}),
                ("analyze", {**ANALYZE_SNAPSHOT, "summary": f"Memory is tight{suffix}"}),
                ("notify", NOTIFY_SNAPSHOT),
                ("ingest", {"alerts_created": 1, "note": f"legacy{suffix}"}),
            ]
        )
    }
    failed = StageExecution.objects.create(
        pipeline_run=run,
        stage="notify",
        status="failed",
        attempt=2,
        started_at=now + timedelta(seconds=9),
        error_message=f"slack 500{suffix}",
    )
    return incident, alert, history, run, stages, failed


def test_the_page_lives_under_the_list():
    assert reverse("console:incident", args=[7]) == "/console/incidents/7/"


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get("/console/incidents/7/")

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/incidents/7/"


@pytest.mark.django_db
def test_anonymous_htmx_request_is_told_to_do_a_full_redirect(client):
    resp = client.get("/console/incidents/7/", **HTMX)

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next=/console/incidents/7/"


@pytest.mark.django_db
def test_non_staff_is_refused(client):
    client.force_login(get_user_model().objects.create_user("visitor", password="x"))
    incident = Incident.objects.create(title="secret")

    resp = client.get(_url(incident))

    assert resp.status_code == 403
    assert "secret" not in resp.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_incident_is_a_console_404_that_links_onward(client, staff, pk, headers):
    resp = client.get(f"/console/incidents/{pk}/", **headers)

    body = resp.content.decode()
    assert resp.status_code == 404
    assert [t.name for t in resp.templates][0] == "console/not_found.html"
    assert "script-src 'self'" in resp["Content-Security-Policy"]
    assert "HX-Request" in resp["Vary"]
    assert "This incident doesn't exist." in body
    back = reverse("console:incidents")
    assert f'<a id="not-found-back" href="{back}">Back to incidents</a>' in body
    assert _nav(body, "Incidents") == f'<a class="c-unav__item" href="{back}" aria-current="page">'
    assert "<title>Not found · Console</title>" in body


@pytest.mark.django_db
def test_staff_gets_the_full_page_with_incidents_current_in_the_nav(client, staff):
    incident = named_incident("Disk full on web-1")

    resp = client.get(_url(incident))

    body = resp.content.decode()
    assert resp.status_code == 200
    assert resp.templates[0].name == "console/incident.html"
    assert "<title>Disk full on web-1 · Console</title>" in body
    current = f'<a class="c-unav__item" href="{reverse("console:incidents")}" aria-current="page">'
    assert _nav(body, "Incidents") == current
    assert "script-src 'self'" in resp["Content-Security-Policy"]
    root = _tag(body, "incident")
    assert re.search(r"\sdata-poll[\s>]", root)
    assert f'hx-get="{_url(incident)}"' in root
    assert 'hx-trigger="every 30s, console:refresh"' in root
    assert 'hx-swap="innerHTML"' in root


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, staff):
    incident = named_incident("Disk full on web-1")

    resp = client.get(_url(incident), **HTMX)

    body = resp.content.decode()
    assert resp.status_code == 200
    assert "<html" not in body
    assert resp.templates[0].name == "console/partials/_incident.html"
    assert 'id="incident"' not in body
    assert "Disk full on web-1" in body


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["HTTP_HX_HISTORY_RESTORE_REQUEST", "HTTP_HX_BOOSTED"])
def test_htmx_request_that_swaps_the_body_gets_the_full_page(client, staff, header):
    incident = Incident.objects.create(title="Disk full on web-1")

    resp = client.get(_url(incident), **HTMX, **{header: "true"})

    assert resp.templates[0].name == "console/incident.html"


@pytest.mark.django_db
@BOTH
def test_the_header_describes_the_incident_and_links_onward(client, staff, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    title = (
        '<h1 class="c-pagehead__title">disk on web-1 '
        f'<span class="c-pagehead__number">#{incident.pk}'
    )
    assert title in body
    assert '<span class="c-label c-tone-critical">critical</span>' in body
    assert re.search(
        r'<span class="c-state c-status--acknowledged"><svg\b.*?</svg>Acknowledged</span>', body
    )
    assert re.search(r'Started <time datetime="[^"]+" title="[^"]+">[^<]+</time>', body)
    assert re.search(r">\d+m?s so far<", body)
    assert "<span>web-1</span>" in body
    assert "<span>1 alert</span>" in body
    assert "<span>pager</span>" in body
    assert '<p class="c-prose">92% used</p>' in body
    assert '<p class="c-prose">Logs rotated</p>' in body
    admin_url = reverse("admin:alerts_incident_change", args=[incident.pk])
    admin = f'<a id="incident-admin" class="c-button c-button--sm" href="{admin_url}">Edit raw</a>'
    assert admin in body
    back = _tag(body, "incident-back")
    assert f'href="{reverse("console:incidents")}"' in back
    assert re.search(r'<a id="incident-back"[^>]*><svg\b.*?</svg>Back to incidents</a>', body)


@pytest.mark.django_db
def test_a_finished_bare_incident_drops_the_optional_header_parts(client, staff):
    now = timezone.now()
    incident = Incident.objects.create(title="Done", status="resolved", resolved_at=now)
    Incident.objects.filter(pk=incident.pk).update(
        created_at=now - timedelta(minutes=4, seconds=12)
    )

    body = client.get(_url(incident)).content.decode()

    assert "4m 12s" in body
    assert "so far" not in body
    assert "0 alerts" in body
    assert "pipeline " not in body
    assert body.count('<span class="c-muted">None</span>') == 2
    assert re.search(
        r'<span class="c-host c-host--lg c-host--unknown">.*?</svg>Unknown host</span>', body
    )
    assert "c-prose" not in body


@pytest.mark.django_db
@BOTH
def test_the_stage_strip_shows_each_stage_with_its_detail_and_runs(client, staff, headers):
    incident, _, _, _, stages, failed = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    check = reverse("admin:orchestration_stageexecution_change", args=[stages["check"].pk])
    assert f'<a id="stage-check" class="c-label c-tone-ok" href="{check}">ok</a>' in body
    notify = reverse("admin:orchestration_stageexecution_change", args=[failed.pk])
    assert (
        f'<a id="stage-notify" class="c-label c-tone-critical" href="{notify}">failed</a>' in body
    )
    assert "error: slack 500 (retryable=True)" in body
    assert "succeeded in 1/1 runs" in body
    for label in ("Diagnose", "Analyze", "Notify"):
        assert f'<span class="c-stage__name">{label}</span>' in body


@pytest.mark.django_db
def test_a_stage_with_no_execution_is_a_plain_label(client, staff):
    incident = Incident.objects.create(title="Fresh")

    body = client.get(_url(incident)).content.decode()

    assert body.count('<span class="c-label c-tone-muted">never ran</span>') == 3
    assert 'id="stage-' not in body


@pytest.mark.django_db
@BOTH
def test_the_timeline_lists_every_entry_with_time_badge_and_title(client, staff, headers):
    incident, alert, history, run, stages, failed = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    assert re.search(r'<ol class="c-timeline" data-print-open>', body)
    assert body.count('<li class="c-timeline__item">') == 10
    alert_url = reverse("admin:alerts_alert_change", args=[alert.pk])
    for row, event in zip(history, ("created", "refired"), strict=True):
        link = f'<a id="entry-alert_history-{row.pk}" href="{alert_url}">Alert disk {event}</a>'
        assert link in body
    assert "— → firing" in body
    run_url = reverse("admin:orchestration_pipelinerun_change", args=[run.pk])
    assert f'<a id="entry-pipeline-{run.pk}" href="{run_url}">Pipeline run started</a>' in body
    assert '<span class="c-muted">notified · run-1</span>' in body
    failed_url = reverse("admin:orchestration_stageexecution_change", args=[failed.pk])
    assert f'<a id="entry-stage-{failed.pk}" href="{failed_url}">Notify failed</a>' in body
    assert "slack 500" in body
    assert '<span id="entry-incident-created">Incident opened</span>' in body
    assert '<span id="entry-incident-acknowledged">Incident acknowledged</span>' in body
    for kind in ("incident", "alert", "run", "stage"):
        assert re.search(rf'<span class="c-label c-tone-[a-z]+">{kind}</span>', body)
    assert "alert_history<" not in body
    assert re.search(
        r'<time class="c-entry__time" datetime="[^"]+" title="[^"]+">'
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d</time>",
        body,
    )
    assert "timeline-all" not in body and "timeline-recent" not in body
    assert "not shown" not in body
    badges = re.findall(r'<span class="c-timeline__badge ([^"]+)">', body)
    assert len(badges) == 10
    assert badges.count("c-status--open") == badges.count("c-status--acknowledged") == 1
    assert "c-tone-critical" in badges


@pytest.mark.django_db
@BOTH
def test_an_entry_with_output_is_a_details_holding_its_body_and_raw_json(client, staff, headers):
    incident, _, _, _, stages, _ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    for name, title in [
        ("check", "Diagnose succeeded in 850ms"),
        ("analyze", "Analyze succeeded in 850ms"),
        ("notify", "Notify succeeded in 850ms"),
        ("ingest", "Ingest succeeded in 850ms"),
    ]:
        entry = f"entry-stage-{stages[name].pk}"
        assert _tag(body, entry) == f'<details id="{entry}">'
        assert _tag(body, f"{entry}-summary").startswith("<summary ")
        assert title in body
        url = reverse("admin:orchestration_stageexecution_change", args=[stages[name].pk])
        assert f'<a id="{entry}-link" href="{url}">Open in admin</a>' in body
        assert _tag(body, f"{entry}-raw") == f'<details id="{entry}-raw" class="c-entry__raw">'
        assert f'<summary id="{entry}-raw-summary">Raw JSON</summary>' in body
        assert _tag(body, f"{entry}-json").startswith("<pre ")
    assert body.count("<details") == 8
    assert "<details open" not in body and " open>" not in body


@pytest.mark.django_db
def test_each_stage_body_is_rendered(client, staff):
    incident, *_ = _busy()

    body = client.get(_url(incident)).content.decode()

    assert "2 run, 1 passed, 1 failed" in body
    assert '<span class="c-label c-tone-warning">warning</span>' in body
    assert "Memory usage: 80.6% (3.9/8.0 GB)" in body
    assert "disk timed out" in body
    assert "Memory is tight" in body
    assert "A leak in the worker." in body
    assert "Restart the worker" in body
    assert "Add swap" in body and "The box has none." in body and "medium" in body
    assert "80% confidence" in body
    assert "Fallback analysis" in body
    assert "2 attempted, 1 succeeded, 1 failed" in body
    assert '<span class="c-label c-tone-ok">success</span>' in body
    assert "slack" in body and "#alerts" in body and "a970" in body
    assert "email: refused" in body
    assert "&quot;channels_attempted&quot;: 2" in body
    assert "&quot;alerts_created&quot;: 1" in body


@pytest.mark.django_db
def test_sparse_stage_bodies_leave_their_optional_parts_out(client, staff):
    incident = Incident.objects.create(title="Sparse")
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    for name, snapshot in [
        ("check", {"checks_run": 0, "checks": [{"name": "cpu"}]}),
        ("analyze", {"summary": "Only this."}),
        ("notify", {"channels_attempted": 0}),
    ]:
        StageExecution.objects.create(
            pipeline_run=run,
            stage=name,
            status="succeeded",
            started_at=timezone.now(),
            output_snapshot=snapshot,
        )

    body = client.get(_url(incident)).content.decode()

    assert '<span class="c-label c-tone-muted">unknown</span>' in body
    assert "Only this." in body
    assert "confidence" not in body.replace("&quot;confidence&quot;", "")
    assert "Fallback analysis" not in body
    assert "Probable cause" not in body
    assert "c-errors" not in body
    assert "0 attempted, 0 succeeded, 0 failed" in body
    assert "Succeeded in" not in body


@pytest.mark.django_db
@BOTH
def test_hostile_strings_are_escaped_everywhere(client, staff, headers):
    incident, *_ = _busy(suffix=f" {MARKUP}")

    body = client.get(_url(incident), **headers).content.decode()

    assert "<img" not in body
    for text in (
        f"disk {ESCAPED} on web-1 {ESCAPED}",
        f"92% used {ESCAPED}",
        f"Logs rotated {ESCAPED}",
        f"web-1 {ESCAPED}",
        f"<span>pager {ESCAPED}</span>",
        f"Alert disk {ESCAPED} created",
        f"disk timed out {ESCAPED}",
        f"Memory is tight {ESCAPED}",
        f"slack 500 {ESCAPED}",
        f"&quot;note&quot;: &quot;legacy {ESCAPED}&quot;",
    ):
        assert text in body, text
    if not headers:
        assert f"<title>disk {ESCAPED} on web-1 {ESCAPED} · Console</title>" in body


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(client, staff, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]

    focusable = re.findall(
        r"<(?:a|summary|details|pre|button|input|select|textarea)\b[^>]*>", region
    )
    assert len(focusable) == 2 + 3 + 4 + 4 * 6
    assert all(re.search(r'\sid="[^"]+"', tag) for tag in focusable)
    ids = re.findall(r'\sid="([^"]*)"', region)
    assert len(ids) == len(set(ids))


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_page_is_clean(client, staff, label, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    assert "c-timeline" in body and "c-stage" in body and "c-raw" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
def test_overview_and_list_rows_link_to_the_console_page(client, staff):
    incident = named_incident("Disk full on web-1", severity="critical")

    for page in ("console:overview", "console:incidents"):
        body = client.get(reverse(page)).content.decode()

        link = f'<a id="incident-{incident.pk}" href="{_url(incident)}">Disk full on web-1</a>'
        assert link in body
        assert reverse("admin:alerts_incident_change", args=[incident.pk]) not in body


def _long_incident(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 2)
    incident = Incident.objects.create(title="Long")
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    for index in range(5):
        StageExecution.objects.create(
            pipeline_run=run,
            stage="check",
            status="succeeded",
            attempt=index + 1,
            started_at=timezone.now() + timedelta(seconds=index),
        )
    return incident


@pytest.mark.django_db
@BOTH
def test_a_long_timeline_is_capped_and_offers_the_rest(client, staff, monkeypatch, headers):
    incident = _long_incident(monkeypatch)

    body = client.get(_url(incident), **headers).content.decode()

    assert body.count('<li class="c-timeline__item">') == 3
    assert re.search(
        r'<li class="c-timeline__note">\s*<span class="c-muted">4 earlier entries not shown\.'
        rf'</span>\s*<a id="timeline-all" href="{re.escape(_url(incident))}\?all=1">Show all</a>',
        body,
    )
    assert "timeline-recent" not in body
    if not headers:
        assert f'hx-get="{_url(incident)}"' in _tag(body, "incident")


@pytest.mark.django_db
def test_a_single_hidden_entry_reads_in_the_singular(client, staff, monkeypatch):
    incident = _long_incident(monkeypatch)
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 5)

    body = client.get(_url(incident)).content.decode()

    assert "1 earlier entry not shown." in body


@pytest.mark.django_db
@BOTH
def test_all_shows_every_entry_and_keeps_polling_with_the_parameter(
    client, staff, monkeypatch, headers
):
    incident = _long_incident(monkeypatch)

    body = client.get(f"{_url(incident)}?all=1", **headers).content.decode()

    assert body.count('<li class="c-timeline__item">') == 7
    assert "not shown" not in body and "timeline-all" not in body
    assert re.search(
        r'<li class="c-timeline__note">\s*<span class="c-muted">Showing all 7 entries\.</span>'
        rf'\s*<a id="timeline-recent" href="{re.escape(_url(incident))}">Show recent only</a>',
        body,
    )
    if not headers:
        assert f'hx-get="{_url(incident)}?all=1"' in _tag(body, "incident")


@pytest.mark.django_db
@pytest.mark.parametrize("query", ["?all=0", "?all=true", "?all=", "?all=1&all=2", "?other=1"])
def test_any_other_all_value_is_off(client, staff, monkeypatch, query):
    incident = _long_incident(monkeypatch)

    body = client.get(f"{_url(incident)}{query}").content.decode()

    assert "timeline-all" in body
    assert f'hx-get="{_url(incident)}"' in _tag(body, "incident")


@pytest.mark.django_db
def test_the_header_names_the_host_right_after_the_state(client, staff):
    incident = Incident.objects.create(title="Disk full", severity="critical")
    Alert.objects.create(
        fingerprint="f",
        name="disk",
        source="cluster",
        labels={"instance_id": "db-7"},
        incident=incident,
        started_at=timezone.now(),
    )

    body = client.get(_url(incident)).content.decode()

    assert re.search(
        r'c-state c-status--open">.*?Open</span>\s*<span class="c-host c-host--lg">.*?</svg>db-7</span>',
        body,
        re.S,
    )
