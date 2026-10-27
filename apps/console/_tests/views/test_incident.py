import re
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, AlertHistory, Incident, Node
from apps.console._tests.queries.test_stages import (
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


def _url(incident):
    return reverse("console:incident", args=[incident.pk])


def _tag(body, element_id):
    return re.search(rf'<[a-z]+\b[^>]*\sid="{re.escape(element_id)}"[^>]*>', body).group()


def _nav(body, label):
    """The opening tag of the underline nav tab named ``label``."""
    return re.search(
        rf'(<a class="c-unav__item" [^>]*>)(?:(?!</a>).)*<span>{label}</span></a>', body, re.S
    ).group(1)


class _FocusAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.focusable = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        hidden = tag == "input" and attrs.get("type") == "hidden"
        if (
            tag in ("button", "select", "textarea", "summary")
            or (tag == "input" and not hidden)
            or (tag == "a" and "href" in attrs)
            or "tabindex" in attrs
        ):
            self.focusable.append((tag, attrs.get("id")))


def focus_audit(html):
    """Every focusable element as ``(tag, id)``, and every id, in document order."""
    audit = _FocusAudit()
    audit.feed(html)
    return audit.focusable, audit.ids


def named_incident(name, **fields):
    """An incident whose one webhook alert gives it ``name`` as its described headline."""
    incident = Incident.objects.create(**fields)
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
        severity="critical",
        status="acknowledged",
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
def test_a_plain_user_is_refused(client):
    client.force_login(get_user_model().objects.create_user("visitor", password="x"))
    incident = Incident.objects.create()

    resp = client.get(_url(incident))

    assert resp.status_code == 403
    assert "secret" not in resp.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("pk", ["404", "99999999999999999999999999"])
@BOTH
def test_a_missing_incident_is_a_console_404_that_links_onward(client, superuser, pk, headers):
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
def test_a_superuser_gets_the_full_page_with_incidents_current_in_the_nav(client, superuser):
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
def test_htmx_request_gets_only_the_partial(client, superuser):
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
def test_htmx_request_that_swaps_the_body_gets_the_full_page(client, superuser, header):
    incident = Incident.objects.create()

    resp = client.get(_url(incident), **HTMX, **{header: "true"})

    assert resp.templates[0].name == "console/incident.html"


@pytest.mark.django_db
@BOTH
def test_the_header_describes_the_incident_and_links_onward(client, superuser, headers):
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
    assert re.search(r'Started <time datetime="[^"]+" title="[^"]+">[^<]+ ago</time>', body)
    assert re.search(r">\d+m?s so far<", body)
    assert "<span>web-1</span>" in body
    assert "<span>1 alert</span>" in body
    assert "<span>pager</span>" in body
    assert '<p class="c-prose">Logs rotated</p>' in body
    back = _tag(body, "incident-back")
    assert f'href="{reverse("console:incidents")}"' in back
    assert re.search(r'<a id="incident-back"[^>]*><svg\b.*?</svg>Back to incidents</a>', body)


@pytest.mark.django_db
@BOTH
def test_whats_wrong_renders_the_incident_description(client, superuser, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    box = body[body.index("What's wrong") : body.index("</section>")]
    assert '<div class="c-description c-tone-critical">' in box
    assert "<span>disk on web-1</span>" in box
    assert '<p class="c-description__detail">92% used</p>' in box
    assert _tag(box, "incident-what-details").startswith("<details ")
    assert "<dt>Fingerprint</dt><dd>check:web-1:disk</dd>" in box


@pytest.mark.django_db
@BOTH
def test_the_page_has_no_edit_raw_and_no_open_in_admin(client, superuser, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    assert "Edit raw" not in body
    assert "Open in admin" not in body
    assert "incident-admin" not in body


@pytest.mark.django_db
@BOTH
def test_the_page_links_no_admin_page(client, superuser, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]

    assert 'href="/admin/' not in region
    assert "Open the run" in region and "Open the alert" in region


@pytest.mark.django_db
def test_a_superuser_gets_navigation_links_into_the_console(client, superuser):
    incident, alert, _, run, *_ = _busy()

    body = client.get(_url(incident), **HTMX).content.decode()

    assert f'href="{reverse("console:alert", args=[alert.pk])}"' in body
    assert f'href="{reverse("console:run", args=[run.pk])}"' in body


@pytest.mark.django_db
def test_a_finished_bare_incident_drops_the_optional_header_parts(client, superuser):
    now = timezone.now()
    incident = Incident.objects.create(status="resolved", resolved_at=now)
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
def test_the_stage_strip_shows_each_stage_outcome(client, superuser, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    for stage, label, tone, headline in [
        ("check", "Diagnose", "ok", "Diagnose finished"),
        ("analyze", "Analyze", "ok", "Analyze finished"),
        ("notify", "Notify", "critical", "Notify failed"),
    ]:
        item = body[body.index(f'<li id="stage-{stage}" class="c-stage">') :]
        item = item[: item.index("</li>")]
        assert f'<span class="c-stage__icon c-tone-{tone}">' in item
        assert f'<span class="c-stage__name">{label}</span>' in item
        assert f'<span class="c-stage__detail">{headline}</span>' in item
    assert "retryable=" not in body


@pytest.mark.django_db
def test_a_stage_with_no_execution_says_it_hasnt_run(client, superuser):
    incident = Incident.objects.create()

    body = client.get(_url(incident)).content.decode()

    for label in ("Diagnose", "Analyze", "Notify"):
        assert f'<span class="c-stage__detail">{label} hasn&#x27;t run yet</span>' in body
    assert body.count('<span class="c-stage__icon c-tone-muted">') == 3


@pytest.mark.django_db
@BOTH
def test_the_timeline_entries_use_the_describer_wording(client, superuser, headers):
    incident, alert, history, run, stages, failed = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    assert re.search(r'<ol class="c-timeline" data-print-open>', body)
    assert body.count('<li class="c-timeline__item">') == 10
    for row, headline in zip(history, ("disk started firing", "disk fired again"), strict=True):
        entry = f"entry-alert_history-{row.pk}"
        assert _tag(body, entry) == f'<details id="{entry}">'
        assert _tag(body, f"{entry}-summary").startswith("<summary ")
        assert f'<span class="c-entry__title">{headline}</span>' in body
    assert "<dt>Status</dt><dd>firing</dd>" in body
    assert '<span class="c-entry__title">Run finished, notified</span>' in body
    assert f'id="entry-pipeline-{run.pk}-what-details-copy-1"' in body
    assert '<span class="c-entry__title">Notify failed</span>' in body
    assert "will be retried" not in body
    assert "<dt>Retryable</dt><dd>yes</dd>" in body
    assert "<dt>Error</dt><dd>slack 500</dd>" in body
    assert "Open the run&#x27;s error in Details" in body
    assert '<span id="entry-incident-created">Incident opened</span>' in body
    assert '<span id="entry-incident-acknowledged">Incident acknowledged</span>' in body
    for kind in ("incident", "alert", "run", "stage"):
        assert re.search(rf'<span class="c-label c-tone-[a-z]+">{kind}</span>', body)
    assert "alert_history<" not in body
    assert re.search(
        r'<span class="c-entry__time"><time datetime="[^"]+" title="[^"]+">[^<]+ ago</time>',
        body,
    )
    assert "timeline-all" not in body and "timeline-recent" not in body
    assert "not shown" not in body
    badges = re.findall(r'<span class="c-timeline__badge ([^"]+)">', body)
    assert len(badges) == 10
    assert badges.count("c-status--open") == badges.count("c-status--acknowledged") == 1
    assert "c-tone-critical" in badges


@pytest.mark.django_db
def test_entry_details_sit_directly_under_their_timeline_item(client, superuser):
    incident, *_ = _busy()

    body = client.get(_url(incident), **HTMX).content.decode()

    items = re.findall(
        r'<li class="c-timeline__item">\s*<span class="c-timeline__badge.*?</li>', body, re.S
    )
    assert len(items) == 10
    disclosures = [i for i in items if re.search(r"</span>\s*<details id=\"entry-", i)]
    assert len(disclosures) == 8


@pytest.mark.django_db
@BOTH
def test_an_entry_with_output_holds_its_body_and_raw_json_in_details(client, superuser, headers):
    incident, _, _, _, stages, _ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    for name, title in [
        ("check", "Diagnose finished in 850ms"),
        ("analyze", "Analyze finished in 850ms"),
        ("notify", "Notify finished in 850ms"),
        ("ingest", "Ingest finished in 850ms"),
    ]:
        entry = f"entry-stage-{stages[name].pk}"
        assert _tag(body, entry) == f'<details id="{entry}">'
        assert _tag(body, f"{entry}-summary").startswith("<summary ")
        assert f'<span class="c-entry__title">{title}</span>' in body
        details = f"{entry}-what-details"
        assert _tag(body, details).startswith(f'<details id="{details}" class="c-details"')
        raw = f"{details}-raw"
        assert _tag(body, raw) == f'<details id="{raw}" class="c-entry__raw">'
        assert f'<summary id="{raw}-summary">Raw JSON</summary>' in body
        assert _tag(body, f"{details}-json").startswith("<pre ")
        assert body.index(raw) > body.index(details)
    assert "<details open" not in body and " open>" not in body


@pytest.mark.django_db
def test_each_stage_body_is_rendered(client, superuser):
    incident, _, _, _, stages, _ = _busy()

    body = client.get(_url(incident)).content.decode()

    check = f"entry-stage-{stages['check'].pk}"
    assert _tag(body, f"{check}-problem-1-details")
    assert "Memory usage: 80.6% (3.9/8.0 GB)" in body
    assert '<p class="c-muted">1 check passed</p>' in body
    assert "The checks hit 1 error" in body and "disk timed out" in body
    assert "AI analysis failed" in body
    assert "Check the provider&#x27;s settings" in body
    assert "Add swap" not in body.replace("&quot;Add swap&quot;", "")
    assert "Sent to #alerts by Slack" in body
    assert "Not sent to ops@example.com" in body
    assert "&quot;channels_attempted&quot;: 2" in body
    assert "&quot;alerts_created&quot;: 1" in body


@pytest.mark.django_db
def test_a_check_body_lists_problems_worst_first_and_counts_the_rest(client, superuser):
    incident = Incident.objects.create()
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    execution = StageExecution.objects.create(
        pipeline_run=run,
        stage="check",
        status="succeeded",
        started_at=timezone.now(),
        output_snapshot={
            "checks": [
                {"name": "cpu", "status": "ok", "message": "fine"},
                {"name": "gpu", "status": "warning", "message": "GPU warm"},
                {"name": "raid", "status": "skipped", "message": "Skipped: no mdadm"},
                {"name": "fan", "status": "critical", "message": "Fan stopped"},
                {"name": "psu", "status": "unknown", "message": "PSU unreadable"},
                {"name": "memory", "status": "ok", "message": "fine"},
                {"name": "disk", "status": "skipped", "message": "Skipped: none"},
            ]
        },
    )

    body = client.get(_url(incident)).content.decode()

    entry = f"entry-stage-{execution.pk}"
    order = [body.index(text) for text in ("Fan stopped", "PSU unreadable", "GPU warm")]
    assert order == sorted(order)
    assert f'id="{entry}-problem-3-details"' in body
    assert f'id="{entry}-problem-4' not in body
    assert '<p class="c-muted">2 checks passed, 2 skipped</p>' in body


@pytest.mark.django_db
def test_a_check_body_with_only_skips_counts_them_alone(client, superuser):
    incident = Incident.objects.create()
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    StageExecution.objects.create(
        pipeline_run=run,
        stage="check",
        status="succeeded",
        started_at=timezone.now(),
        output_snapshot={"checks": [{"name": "raid", "status": "skipped"}]},
    )

    body = client.get(_url(incident)).content.decode()

    assert '<p class="c-muted">1 skipped</p>' in body
    assert "passed" not in body


@pytest.mark.django_db
def test_a_failed_analysis_shows_no_confidence_anywhere(client, superuser):
    incident = Incident.objects.create()
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    StageExecution.objects.create(
        pipeline_run=run,
        stage="analyze",
        status="succeeded",
        started_at=timezone.now(),
        output_snapshot={
            "summary": "Guessing.",
            "confidence": 0.9,
            "fallback_used": True,
            "failure": "timeout",
            "recommendations": [{"title": "Add swap"}],
        },
    )

    body = client.get(_url(incident)).content.decode()

    assert "AI analysis failed: the provider didn&#x27;t answer in time" in body
    assert "confidence" not in body.lower().replace("&quot;confidence&quot;", "")
    assert "Add swap" not in body.replace("&quot;Add swap&quot;", "")


@pytest.mark.django_db
def test_a_successful_analysis_lists_its_recommendations(client, superuser):
    incident = Incident.objects.create()
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    StageExecution.objects.create(
        pipeline_run=run,
        stage="analyze",
        status="succeeded",
        started_at=timezone.now(),
        output_snapshot={**ANALYZE_SNAPSHOT, "fallback_used": False, "failure": ""},
    )

    body = client.get(_url(incident)).content.decode()

    assert "<span>Memory is tight on web-1.</span>" in body
    assert "A leak in the worker." in body
    assert "<li>Restart the worker</li>" in body
    assert "<strong>Add swap</strong> The box has none." in body


def _no_channel_incident():
    incident = Incident.objects.create()
    run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
    StageExecution.objects.create(
        pipeline_run=run,
        stage="notify",
        status="failed",
        started_at=timezone.now(),
        error_message="no_channel: the matched lane names no active notification channel",
    )
    return incident


@pytest.mark.django_db
def test_a_no_channel_failure_links_its_fix_for_a_superuser(client, superuser):
    incident = _no_channel_incident()

    body = client.get(_url(incident)).content.decode()

    route = reverse("console:routes")
    assert re.search(
        rf'<a id="entry-stage-\d+-what-action" class="c-button c-button--sm" href="{route}">'
        r"Give the route an active channel</a>",
        body,
    )
    assert "Not delivered: the matched route has no active channel" in body


def _stage_item(body, stage):
    item = body[body.index(f'<li id="stage-{stage}" class="c-stage">') :]
    return item[: item.index("</li>")]


@pytest.mark.django_db
def test_a_failed_stage_shows_its_fix_in_the_sidebar(client, superuser):
    incident = _no_channel_incident()

    item = _stage_item(client.get(_url(incident)).content.decode(), "notify")

    route = reverse("console:routes")
    assert (
        f'<a id="stage-notify-action" class="c-button c-button--sm" href="{route}">'
        "Give the route an active channel</a>"
    ) in item
    assert "c-details" not in item


@pytest.mark.django_db
def test_a_stage_that_hasnt_run_offers_no_action(client, superuser):
    incident = Incident.objects.create()

    body = client.get(_url(incident)).content.decode()

    assert "c-stage__action" not in body


@pytest.mark.django_db
def test_sparse_stage_bodies_leave_their_optional_parts_out(client, superuser):
    incident = Incident.objects.create()
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

    assert "CPU usage could not be checked" in body
    assert "<span>Only this.</span>" in body
    assert "c-bullets" not in body and "c-lines" not in body
    assert "Nothing was sent" in body
    assert "passed" not in body and "skipped" not in body
    assert "finished in" not in body


@pytest.mark.django_db
@BOTH
def test_hostile_strings_are_escaped_everywhere(client, superuser, headers):
    incident, *_ = _busy(suffix=f" {MARKUP}")

    body = client.get(_url(incident), **headers).content.decode()

    assert "<img" not in body
    for text in (
        f"disk {ESCAPED} on web-1 {ESCAPED}",
        f"92% used {ESCAPED}",
        f"Logs rotated {ESCAPED}",
        f"web-1 {ESCAPED}",
        f"<span>pager {ESCAPED}</span>",
        f"disk {ESCAPED} started firing",
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
def test_every_focusable_element_in_the_polled_region_has_a_unique_id(client, superuser, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert len(focusable) > 20
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))


@pytest.mark.django_db
@BOTH
def test_every_focusable_element_has_a_unique_id_with_the_action_buttons(
    client, superuser, headers
):
    incident, _, _, run, *_ = _busy()
    StageExecution.objects.create(
        pipeline_run=run,
        stage="notify",
        status="failed",
        attempt=3,
        started_at=timezone.now() + timedelta(seconds=20),
        error_message="no_channel: x",
    )
    Incident.objects.filter(pk=incident.pk).update(status="open")

    body = client.get(_url(incident), **headers).content.decode()
    region = body if headers else body[body.index('<div id="incident"') : body.index("</main>")]

    focusable, ids = focus_audit(region)
    assert [tag for tag, element_id in focusable if not element_id] == []
    assert len(ids) == len(set(ids))
    assert {"action-acknowledge", "action-resolve", "action-close"} <= set(ids)


@pytest.mark.django_db
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
@BOTH
def test_the_rendered_page_is_clean(client, superuser, label, headers):
    incident, *_ = _busy()

    body = client.get(_url(incident), **headers).content.decode()

    assert "c-timeline" in body and "c-stage" in body and "c-raw" in body
    assert not FORBIDDEN[label].search(body), label


@pytest.mark.django_db
def test_overview_and_list_rows_link_to_the_console_page(client, superuser):
    incident = named_incident("Disk full on web-1", severity="critical")

    for page in ("console:overview", "console:incidents"):
        body = client.get(reverse(page)).content.decode()

        link = f'<a id="incident-{incident.pk}" href="{_url(incident)}">Disk full on web-1</a>'
        assert link in body
        assert reverse("admin:alerts_incident_change", args=[incident.pk]) not in body


def _long_incident(monkeypatch):
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 2)
    incident = Incident.objects.create()
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
def test_a_long_timeline_is_capped_and_offers_the_rest(client, superuser, monkeypatch, headers):
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
def test_a_single_hidden_entry_reads_in_the_singular(client, superuser, monkeypatch):
    incident = _long_incident(monkeypatch)
    monkeypatch.setattr(incident_queries, "TIMELINE_LIMIT", 5)

    body = client.get(_url(incident)).content.decode()

    assert "1 earlier entry not shown." in body


@pytest.mark.django_db
@BOTH
def test_all_shows_every_entry_and_keeps_polling_with_the_parameter(
    client, superuser, monkeypatch, headers
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
def test_any_other_all_value_is_off(client, superuser, monkeypatch, query):
    incident = _long_incident(monkeypatch)

    body = client.get(f"{_url(incident)}{query}").content.decode()

    assert "timeline-all" in body
    assert f'hx-get="{_url(incident)}"' in _tag(body, "incident")


@pytest.mark.django_db
def test_the_header_names_the_host_right_after_the_state(client, superuser):
    incident = Incident.objects.create(severity="critical")
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
