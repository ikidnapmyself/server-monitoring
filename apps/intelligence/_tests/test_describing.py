import re

import pytest

from apps.intelligence.describing import (
    FAILURES,
    LOCAL_ANALYZER,
    RUNNING,
    UNKNOWN_PROVIDER,
    analysis_failed,
    describe_analysis,
    describe_analysis_run,
    describe_recommendations,
    provider_label,
)
from apps.intelligence.models import AnalysisRun
from config.describing import Description, Fact
from config.failures import FAILURE_KINDS


def facts(d):
    return [(f.label, f.value) for f in d.facts]


def _failed(kind, **extra):
    return {
        "summary": "",
        "probable_cause": "",
        "confidence": None,
        "fallback_used": True,
        "failure": kind,
        "error_detail": "",
        "model_info": {"provider": "claude"},
        **extra,
    }


def test_every_failure_kind_is_described():
    assert set(FAILURE_KINDS) <= set(FAILURES)
    for kind in FAILURE_KINDS:
        d = describe_analysis(_failed(kind))
        assert d.headline.startswith("AI analysis failed")
        assert d.tone == "warning"
        assert d.action is not None


@pytest.mark.parametrize(
    "kind, headline, intent",
    [
        ("auth", "AI analysis failed: the provider rejected the API key", "edit_provider"),
        ("rate_limit", "AI analysis failed: the provider is rate limiting this hub", ""),
        ("timeout", "AI analysis failed: the provider didn't answer in time", ""),
        ("provider_down", "AI analysis failed: the provider is unreachable", ""),
        ("rejected", "AI analysis failed: the provider refused the request", "edit_provider"),
        ("unknown", "AI analysis failed", "edit_provider"),
    ],
)
def test_failure_kinds(kind, headline, intent):
    d = describe_analysis(_failed(kind))
    assert d.headline == headline
    assert d.action.intent == intent


def test_rate_limit_says_wait():
    assert describe_analysis(_failed("rate_limit")).action.text == "Wait, or raise the plan's limit"


def test_failure_without_fallback_flag_still_fails():
    d = describe_analysis({"failure": "auth", "fallback_used": False})
    assert d.headline == "AI analysis failed: the provider rejected the API key"


def test_fallback_with_unrecognised_failure_is_generic():
    d = describe_analysis(_failed("gremlins"))
    assert d.headline == "AI analysis failed"
    assert d.action.intent == "edit_provider"


def test_failure_that_is_not_a_string_is_generic():
    d = describe_analysis({"fallback_used": True, "failure": 401})
    assert d.headline == "AI analysis failed"


def test_legacy_failed_snapshot_reads_failed():
    d = describe_analysis(
        {
            "summary": "AI analysis unavailable",
            "probable_cause": "",
            "confidence": 0.0,
            "fallback_used": True,
            "model_info": {"provider": "openai"},
        }
    )
    assert d.headline == "AI analysis failed"
    assert d.tone == "warning"
    assert "Confidence" not in [f.label for f in d.facts]


def test_failure_facts_carry_error_and_provider_but_no_confidence():
    d = describe_analysis(_failed("auth", error_detail="401 invalid x-api-key", confidence=0.9))
    assert facts(d) == [("Provider", "claude"), ("Error", "401 invalid x-api-key")]


def test_success_reads_the_summary():
    d = describe_analysis(
        {
            "summary": "Disk filling",
            "probable_cause": "logs",
            "fallback_used": False,
            "failure": "",
            "confidence": None,
            "model_info": {"provider": "claude"},
        }
    )
    assert d.headline == "Disk filling"
    assert d.tone == "info"
    assert d.detail == "logs"
    assert d.action is None
    assert facts(d) == [("Provider", "claude")]


def test_reported_confidence_is_a_fact():
    d = describe_analysis({"summary": "Disk filling", "failure": "", "confidence": 0.82})
    assert facts(d) == [("Confidence", "82%")]


@pytest.mark.parametrize("confidence", [None, True, "0.8", float("nan"), float("inf")])
def test_unreal_confidence_is_dropped(confidence):
    d = describe_analysis({"summary": "Disk filling", "failure": "", "confidence": confidence})
    assert facts(d) == []


def test_legacy_success_confidence_was_never_real():
    d = describe_analysis({"summary": "Disk filling", "confidence": 0.8, "fallback_used": False})
    assert facts(d) == []


@pytest.mark.parametrize(
    "snapshot",
    [
        {"summary": "", "failure": "", "fallback_used": False},
        {},
        None,
        [],
        "summary",
        {"summary": ["Disk"], "model_info": "claude", "error_detail": 5},
    ],
)
def test_nothing_to_add(snapshot):
    d = describe_analysis(snapshot)
    assert d.headline == "AI analysis found nothing to add"
    assert d.tone == "muted"
    assert d.action is None
    assert d.facts == ()


def test_odd_shapes_on_a_failure_never_raise():
    d = describe_analysis({"fallback_used": True, "model_info": ["x"], "error_detail": {"a": 1}})
    assert d.headline == "AI analysis failed"
    assert d.facts == ()


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
_SNAKE = re.compile(r"\b[a-z]+_[a-z_]+\b")


@pytest.mark.parametrize("kind", [*FAILURE_KINDS, "gremlins", ""])
def test_failure_wording_is_plain(kind):
    d = describe_analysis(_failed(kind))
    for text in (d.headline, d.detail, d.action.text):
        assert not _UUID.search(text) and not _SNAKE.search(text) and "{" not in text


def test_nothing_to_add_wording_is_plain():
    d = describe_analysis(None)
    for text in (d.headline, d.detail):
        assert not _UUID.search(text) and not _SNAKE.search(text) and "{" not in text


@pytest.mark.parametrize(
    "snapshot, failed",
    [
        ({"fallback_used": True}, True),
        ({"failure": "auth"}, True),
        ({"fallback_used": False, "failure": ""}, False),
        ({"fallback_used": "yes"}, False),
        ({}, False),
        (None, False),
        (["fallback_used"], False),
    ],
)
def test_analysis_failed(snapshot, failed):
    assert analysis_failed(snapshot) is failed


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("openai", "OpenAI"),
        ("claude", "Claude (Anthropic)"),
        ("local", LOCAL_ANALYZER),
        ("retired_one", UNKNOWN_PROVIDER),
        ("", UNKNOWN_PROVIDER),
    ],
)
def test_provider_label(key, label):
    assert provider_label(key) == label


@pytest.mark.parametrize("status", ["pending", "running"])
def test_an_unfinished_run_is_still_running(status):
    described = describe_analysis_run(AnalysisRun(status=status, provider="openai"))

    assert described == Description(RUNNING, "muted", facts=(Fact("Provider", "OpenAI"),))


def test_a_failed_run_reads_as_an_unknown_failure_with_its_error():
    run = AnalysisRun(status="failed", provider="claude", error_message="HTTP 500 from upstream")

    described = describe_analysis_run(run)

    headline, action = FAILURES["unknown"]
    assert (described.headline, described.tone, described.action) == (headline, "warning", action)
    assert facts(described) == [
        ("Provider", "Claude (Anthropic)"),
        ("Error", "HTTP 500 from upstream"),
    ]
    assert facts(describe_analysis_run(AnalysisRun(status="failed", provider="x"))) == [
        ("Provider", UNKNOWN_PROVIDER)
    ]


def test_a_succeeded_run_reads_like_its_snapshot():
    run = AnalysisRun(
        status="succeeded",
        provider="local",
        summary="Disk filled by logs",
        probable_cause="Log rotation is off",
        confidence=0.8,
    )

    described = describe_analysis_run(run)

    assert described.headline == "Disk filled by logs"
    assert described.detail == "Log rotation is off"
    assert ("Provider", LOCAL_ANALYZER) in facts(described)
    assert ("Confidence", "80%") in facts(described)


def test_a_succeeded_run_on_the_fallback_reads_as_failed():
    run = AnalysisRun(status="succeeded", provider="openai", summary="x", fallback_used=True)

    assert describe_analysis_run(run).headline == FAILURES["unknown"][0]


def test_recommendations_skip_what_they_cant_read():
    items = [
        {"title": "Rotate logs", "description": "Enable logrotate", "priority": "high"},
        {"title": " Watch it ", "priority": "low"},
        {"title": "Page someone", "priority": "critical"},
        {"title": "Tidy up", "priority": "medium", "description": 7},
        {"title": "", "priority": "high"},
        {"description": "no title"},
        "just a string",
        None,
    ]

    assert describe_recommendations(items) == (
        Description("Rotate logs", "warning", detail="Enable logrotate"),
        Description("Watch it", "info"),
        Description("Page someone", "warning"),
        Description("Tidy up", "info"),
    )
    assert describe_recommendations({"title": "x"}) == ()
    assert describe_recommendations(None) == ()
