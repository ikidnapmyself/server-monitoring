import re

import pytest

from apps.orchestration.describing import (
    REASONS,
    describe_run,
    describe_run_fields,
    describe_stage_diagnosis,
    describe_stage_outcome,
    reason_code,
    run_link,
)
from apps.orchestration.errors import ROUTING_GAPS, routing_gap
from apps.orchestration.models import PipelineRun
from config.describing import Action


def facts(d):
    return [(f.label, f.value) for f in d.facts]


def test_every_routing_gap_has_a_reason():
    assert set(ROUTING_GAPS) <= set(REASONS)


def test_routing_gap_rejects_an_unknown_code():
    with pytest.raises(ValueError):
        routing_gap("notify", "no_luck", "x")


@pytest.mark.parametrize(
    "message, code",
    [
        ("no_route: no active pipeline matched this alert", "no_route"),
        ("no_channel: the matched lane names no active notification channel", "no_channel"),
        ("Stage routing failed: no_route: no active pipeline matched this alert", "no_route"),
        ("Stage notify failed: no_driver: 'x' is not a registered driver", "no_driver"),
        ("Stage notify failed: Delivery failed: boom", None),
        ("Stage notify failed: ", None),
        ("Check error: boom", None),
        ("disk_linux: df failed", None),
        ("Stage check failed: disk_linux: df failed", None),
        ("", None),
        (None, None),
    ],
)
def test_reason_code(message, code):
    assert reason_code(message) == code


def test_no_channel_names_its_fix():
    d = describe_stage_outcome("notify", "failed", "no_channel: x", retryable=False)
    assert d.headline == "Not delivered: the matched route has no active channel"
    assert d.action.intent == "edit_route"
    assert d.tone == "critical"


def test_known_reason_keeps_its_detail_as_a_fact_not_in_the_headline():
    d = describe_stage_outcome("notify", "failed", "no_channel: x", retryable=False)
    assert ("Error", "x") in facts(d)
    assert "x" not in d.headline.split()


def test_known_reason_on_a_stage_without_a_prefix_names_the_stage():
    d = describe_stage_outcome("ingest", "failed", "no_route: x", retryable=False)
    assert d.headline == "Ingest failed: no route matches this incident"


def test_unknown_failure_keeps_the_message_in_details():
    d = describe_stage_outcome("check", "failed", "Check error: boom", retryable=True)
    assert d.headline == "Diagnose failed"
    assert facts(d) == [("Error", "Check error: boom"), ("Retryable", "yes")]
    assert d.action.text == "Open the run's error in Details"


def test_unretryable_failure_without_a_message_has_no_error_fact():
    d = describe_stage_outcome("analyze", "failed", "", retryable=False)
    assert d.headline == "Analyze failed"
    assert facts(d) == [("Retryable", "no")]


@pytest.mark.parametrize(
    "stage, status, headline, tone",
    [
        ("check", "succeeded", "Diagnose finished", "ok"),
        ("analyze", "skipped", "Analyze skipped", "muted"),
        ("notify", "running", "Notify is running", "info"),
        ("notify", "retrying", "Notify is retrying", "info"),
        ("notify", "pending", "Notify is waiting", "muted"),
    ],
)
def test_plain_outcomes(stage, status, headline, tone):
    d = describe_stage_outcome(stage, status, "", retryable=False)
    assert (d.headline, d.tone) == (headline, tone)


def test_skip_reason_goes_into_detail():
    d = describe_stage_outcome("notify", "skipped", "Skipped: notify disabled", retryable=False)
    assert d.detail == "notify disabled"


def test_unknown_stage_capitalises_its_key():
    assert describe_stage_outcome("enrich", "succeeded", "", retryable=False).headline == (
        "Enrich finished"
    )


def test_unknown_status_is_muted_and_in_words():
    d = describe_stage_outcome("check", "on_hold", "", retryable=False)
    assert (d.headline, d.tone) == ("Diagnose on hold", "muted")


def _run(status, message=""):
    return PipelineRun.objects.create(
        trace_id="trace-1", run_id="run-1", status=status, last_error_message=message
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, headline, tone",
    [
        ("notified", "Run finished, notified", "ok"),
        ("pending", "Run queued", "muted"),
        ("processing", "Run in progress", "info"),
        ("ingested", "Run in progress, ingest done", "info"),
        ("checked", "Run in progress, diagnose done", "info"),
        ("analyzed", "Run in progress, analyze done", "info"),
        ("retrying", "Run will be retried", "info"),
        ("skipped", "Run skipped", "muted"),
        ("on_hold", "Run on hold", "muted"),
    ],
)
def test_run_outcomes(status, headline, tone):
    d = describe_run(_run(status))
    assert (d.headline, d.tone) == (headline, tone)


@pytest.mark.django_db
def test_run_carries_its_ids_and_status():
    d = describe_run(_run("notified"))
    assert ("Run", "run-1", True) in [(f.label, f.value, f.copy) for f in d.facts]
    assert ("Trace", "trace-1", True) in [(f.label, f.value, f.copy) for f in d.facts]
    assert ("Status", "notified") in facts(d)


@pytest.mark.django_db
def test_run_failed_on_a_routing_gap_names_its_fix():
    d = describe_run(_run("failed", "no_route: no active pipeline matched this alert"))
    assert d.headline == "Run failed: no route matches this incident"
    assert d.action.intent == "edit_route"
    assert d.tone == "critical"
    assert ("Error", "no active pipeline matched this alert") in facts(d)


@pytest.mark.parametrize("code", ROUTING_GAPS)
def test_run_failed_as_the_orchestrator_stores_it_names_its_fix(code):
    message = str(routing_gap("notify", code, "the detail"))
    d = describe_run_fields(run_id="r", trace_id="t", status="failed", error_message=message)
    assert d.headline == f"Run failed: {REASONS[code].headline}"
    assert d.action == REASONS[code].action
    assert ("Error", "the detail") in facts(d)


def test_run_failed_with_a_stage_prefix_and_no_code_keeps_the_message():
    message = "Stage analyze failed: Analyze error: boom"
    d = describe_run_fields(run_id="r", trace_id="t", status="failed", error_message=message)
    assert d.headline == "Run failed"
    assert ("Error", message) in facts(d)


@pytest.mark.django_db
def test_run_failed_otherwise_keeps_the_message():
    d = describe_run(_run("failed", "Check error: boom"))
    assert d.headline == "Run failed"
    assert ("Error", "Check error: boom") in facts(d)


@pytest.mark.django_db
def test_run_failed_without_a_message_has_no_error_fact():
    d = describe_run(_run("failed"))
    assert d.headline == "Run failed"
    assert "Error" not in [f.label for f in d.facts]


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


@pytest.mark.parametrize("code", sorted(REASONS))
def test_reason_wording_is_plain(code):
    d = describe_stage_outcome("notify", "failed", f"{code}: x", retryable=False)
    for text in (d.headline, d.detail, d.action.text):
        assert not _UUID.search(text) and not _SNAKE.search(text) and "{" not in text


def test_a_finished_stage_says_how_long_it_took():
    d = describe_stage_outcome("check", "succeeded", "", retryable=False, duration_ms=2400.0)
    assert d.headline == "Diagnose finished in 2s"


@pytest.mark.parametrize("duration_ms", [None, 0, 0.0])
def test_a_stage_without_a_duration_says_nothing_of_time(duration_ms):
    d = describe_stage_outcome("check", "succeeded", "", retryable=False, duration_ms=duration_ms)
    assert d.headline == "Diagnose finished"


def test_only_a_finished_stage_reads_its_duration():
    d = describe_stage_outcome("notify", "failed", "boom", retryable=False, duration_ms=2400.0)
    assert d.headline == "Notify failed"


def test_run_link_opens_the_run():
    assert run_link(7) == Action("Open the run", intent="view_run", params={"pk": 7})


def _diagnosis(**fields):
    return {
        "stage": "check",
        "status": "never_ran",
        "runs": None,
        "run_pk": None,
        "execution_status": None,
        "error_message": "",
        "retryable": False,
        **fields,
    }


@pytest.mark.parametrize(
    "status, headline, tone",
    [
        ("never_ran", "Diagnose hasn't run yet", "muted"),
        ("empty", "Diagnose finished with nothing to report", "warning"),
        ("stalled", "Diagnose stalled", "warning"),
    ],
)
def test_diagnosis_states_the_stage_describer_doesnt_know(status, headline, tone):
    d = describe_stage_diagnosis(_diagnosis(status=status))
    assert (d.headline, d.tone, d.action, d.facts) == (headline, tone, None, ())


def test_a_diagnosis_that_ran_reads_like_its_execution():
    entry = _diagnosis(
        status="ok", execution_status="succeeded", run_pk=4, runs="succeeded in 1/2 runs"
    )
    d = describe_stage_diagnosis(entry)
    assert (d.headline, d.tone) == ("Diagnose finished", "ok")
    assert d.action == run_link(4)
    assert facts(d) == [("Runs", "succeeded in 1/2 runs")]


def test_a_diagnosed_failure_keeps_its_fix_over_the_run_link():
    entry = _diagnosis(
        stage="notify",
        status="failed",
        execution_status="failed",
        error_message="no_channel: x",
        run_pk=4,
    )
    d = describe_stage_diagnosis(entry)
    assert d.action.intent == "edit_route"


def test_a_retryable_diagnosed_failure_promises_nothing_and_records_the_flag():
    entry = _diagnosis(
        status="failed", execution_status="failed", error_message="boom", retryable=True
    )
    d = describe_stage_diagnosis(entry)
    assert d.headline == "Diagnose failed"
    assert ("Retryable", "yes") in facts(d)


@pytest.mark.parametrize("status", ["succeeded", "skipped", "running", "pending"])
def test_only_a_failure_carries_the_retryable_fact(status):
    d = describe_stage_outcome("check", status, "", retryable=True)
    assert "Retryable" not in [f.label for f in d.facts]


def test_a_known_reason_records_the_retryable_flag():
    d = describe_stage_outcome("notify", "failed", "no_channel: x", retryable=False)
    assert facts(d) == [("Error", "x"), ("Retryable", "no")]


def test_a_config_skip_reads_through_the_stage_describer():
    entry = _diagnosis(
        stage="analyze",
        status="skipped",
        execution_status="skipped",
        error_message="Skipped: not in this route's stages",
    )
    d = describe_stage_diagnosis(entry)
    assert (d.headline, d.detail, d.action) == (
        "Analyze skipped",
        "not in this route's stages",
        None,
    )


def test_run_fields_read_like_the_run():
    d = describe_run_fields(run_id="r", trace_id="t", status="failed", error_message="no_route: x")
    assert d == describe_run(
        PipelineRun(run_id="r", trace_id="t", status="failed", last_error_message="no_route: x")
    )
