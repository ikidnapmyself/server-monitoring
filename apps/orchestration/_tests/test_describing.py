import re

import pytest

from apps.orchestration.describing import REASONS, describe_run, describe_stage_outcome, reason_code
from apps.orchestration.errors import ROUTING_GAPS, routing_gap
from apps.orchestration.models import PipelineRun


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
        ("Check error: boom", ""),
        ("", ""),
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
    assert d.headline == "Diagnose failed and will be retried"
    assert ("Error", "Check error: boom") in facts(d)
    assert d.action.text == "Open the run's error in Details"


def test_unretryable_failure_without_a_message_has_no_error_fact():
    d = describe_stage_outcome("analyze", "failed", "", retryable=False)
    assert d.headline == "Analyze failed"
    assert d.facts == ()


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
