import json
from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.alerts.models import Node
from apps.console._tests.fixtures import CHECK_SNAPSHOT
from apps.console._tests.queries.test_runs import _alert, _incident, _run
from apps.console.queries.alert import IncidentCard
from apps.console.queries.run import build_run_detail, run_exists
from apps.console.queries.stages import CheckBody
from apps.orchestration.describing import describe_run, describe_stage_outcome
from apps.orchestration.models import StageExecution
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db


def _execution(run, stage, attempt, status="succeeded", started_at=None, **fields):
    return StageExecution.objects.create(
        pipeline_run=run,
        stage=stage,
        attempt=attempt,
        status=status,
        started_at=started_at,
        **fields,
    )


def test_a_missing_run_is_none():
    assert build_run_detail(999) is None
    assert run_exists(999) is False


def test_the_page_carries_the_runs_description_and_incident_card():
    incident = _incident()
    run = _run(incident=incident, origin="manual", current_stage="notify", total_attempts=2)

    detail = build_run_detail(run.pk)

    assert run_exists(run.pk) is True
    assert detail.pk == run.pk
    assert detail.what.headline == describe_run(run).headline
    assert isinstance(detail.incident, IncidentCard)
    assert detail.incident.url == f"/console/incidents/{incident.pk}/"
    assert (detail.origin, detail.stage, detail.attempts) == ("Manual / CLI", "Notify", 2)
    assert detail.created_at == run.created_at
    assert detail.url == f"/console/runs/{run.pk}/"
    assert detail.timeline == ()


def test_a_run_without_an_incident_has_no_card():
    assert build_run_detail(_run().pk).incident is None


def test_the_node_is_the_linked_node_else_the_incidents_hosts():
    web = Node.objects.create(instance_id="i-web", hostname="web")
    incident = _incident()
    _alert(incident, labels={"instance_id": "i-a"})
    _alert(incident, labels={"instance_id": "i-b"})

    assert build_run_detail(_run(node=web, incident=incident).pk).node == "web"
    assert build_run_detail(_run(incident=incident).pk).node == "i-a, i-b"
    assert build_run_detail(_run().pk).node == ""


def test_every_attempt_is_a_timeline_entry_in_order():
    run = _run(status="notified")
    t0 = timezone.now()
    check = _execution(run, "check", 1, started_at=t0, output_snapshot=CHECK_SNAPSHOT)
    failed = [
        _execution(
            run,
            "notify",
            n,
            status="failed",
            started_at=t0 + timedelta(seconds=n),
            error_message="slack 500",
        )
        for n in (1, 2)
    ]
    sent = _execution(run, "notify", 3, started_at=t0 + timedelta(seconds=3), duration_ms=1200.0)

    timeline = build_run_detail(run.pk).timeline

    executions = [check, *failed, sent]
    assert [entry.id for entry in timeline] == [f"stage-{e.pk}" for e in executions]
    for entry, execution in zip(timeline, executions, strict=True):
        assert entry.kind == "stage"
        assert entry.when == execution.started_at
        what = describe_stage_outcome(
            execution.stage,
            execution.status,
            execution.error_message,
            retryable=execution.error_retryable,
            duration_ms=execution.duration_ms,
        )
        assert entry.what == replace(
            what, facts=(*what.facts, Fact("Attempt", str(execution.attempt)))
        )
    assert isinstance(timeline[0].body, CheckBody)
    assert timeline[3].what.headline == "Notify finished in 1s"


def test_an_execution_that_never_started_sorts_last_and_uses_the_run_time():
    run = _run(status="pending")
    waiting = _execution(run, "notify", 1, status="pending")
    ran = _execution(run, "check", 1, started_at=timezone.now())

    timeline = build_run_detail(run.pk).timeline

    assert [entry.id for entry in timeline] == [f"stage-{ran.pk}", f"stage-{waiting.pk}"]
    assert timeline[1].when == run.created_at
    assert timeline[1].body is None


@pytest.mark.parametrize("can_act", [True, False])
def test_a_failed_run_offers_retry_only_to_a_user_who_may_act(can_act):
    run = _run(status="failed")

    detail = build_run_detail(run.pk, can_act=can_act)

    assert detail.do == ((Action("Retry", "retry_run", {"pk": run.pk}),) if can_act else ())


@pytest.mark.parametrize("status", ["pending", "processing", "retrying"])
def test_an_open_run_offers_mark_failed(status):
    run = _run(status=status)

    assert build_run_detail(run.pk, can_act=True).do == (
        Action("Mark failed", "abandon_run", {"pk": run.pk}),
    )
    assert build_run_detail(run.pk).do == ()


@pytest.mark.parametrize("status", ["notified", "analyzed", "checked", "ingested", "skipped"])
def test_a_finished_run_offers_nothing(status):
    assert build_run_detail(_run(status=status).pk, can_act=True).do == ()


def test_details_hold_the_ids_refs_error_and_payload():
    run = _run(
        status="failed",
        alert_fingerprint="fp-1",
        normalized_payload_ref="ref-normal",
        checker_output_ref="ref-check",
        intelligence_output_ref="ref-analysis",
        notify_output_ref="ref-notify",
        last_error_type="StageExecutionError",
        last_error_message="boom",
        last_error_retryable=False,
        inbound_payload={"b": 1, "a": {"c": 2}},
    )

    detail = build_run_detail(run.pk)

    facts = {fact.label: fact for fact in detail.what.facts}
    assert {
        "Run",
        "Trace",
        "Status",
        "Fingerprint",
        "Normalized payload",
        "Checker output",
        "Analysis output",
        "Notify output",
        "Error type",
        "Retryable",
    } <= facts.keys()
    assert facts["Fingerprint"].copy is True
    assert (facts["Retryable"].value, facts["Analysis output"].value) == ("no", "ref-analysis")
    assert detail.raw == json.dumps({"a": {"c": 2}, "b": 1}, indent=2, sort_keys=True)


def test_an_empty_ref_has_no_fact_and_a_clean_run_no_error():
    run = _run(status="notified", checker_output_ref="ref-check", last_error_retryable=True)

    labels = [fact.label for fact in build_run_detail(run.pk).what.facts]

    assert "Checker output" in labels
    for absent in ("Fingerprint", "Normalized payload", "Notify output", "Error type"):
        assert absent not in labels


def test_a_retryable_error_says_yes():
    run = _run(status="failed", last_error_type="StageExecutionError", last_error_retryable=True)

    facts = {fact.label: fact.value for fact in build_run_detail(run.pk).what.facts}

    assert facts["Retryable"] == "yes"


def _queries(pk):
    with CaptureQueriesContext(connection) as captured:
        build_run_detail(pk, can_act=True)
    return len(captured)


def test_the_query_count_does_not_grow_with_the_attempts():
    incident = _incident()
    _alert(incident)
    run = _run(incident=incident, status="failed")
    _execution(run, "check", 1, started_at=timezone.now())
    few = _queries(run.pk)
    for n in range(1, 10):
        _execution(run, "notify", n, status="failed", started_at=timezone.now())

    assert _queries(run.pk) == few
