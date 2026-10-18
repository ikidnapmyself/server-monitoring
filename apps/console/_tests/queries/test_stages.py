import json

import pytest

from apps.checkers.checkers import CheckStatus
from apps.checkers.describing import describe_check
from apps.console.queries.stages import (
    AnalyzeBody,
    NotifyBody,
    RawBody,
    Recommendation,
    stage_body,
)
from apps.intelligence.describing import describe_analysis
from apps.notify.describing import describe_notify
from config.describing import Description

CHECK_SNAPSHOT = {
    "alert_id": 15,
    "incident_id": 13,
    "checks": [
        {
            "name": "cpu",
            "status": "ok",
            "message": "CPU usage: 21.8% (avg of 5 samples)",
            "metrics": {"cpu_percent": 21.8},
        },
        {
            "name": "memory",
            "status": "warning",
            "message": "Memory usage: 80.6% (3.9/8.0 GB)",
            "metrics": {"percent": 80.6},
        },
    ],
    "checks_run": 2,
    "checks_passed": 1,
    "checks_failed": 1,
    "errors": ["disk: timed out"],
    "duration_ms": 12.5,
}
ANALYZE_SNAPSHOT = {
    "summary": "Memory is tight on web-1.",
    "probable_cause": "A leak in the worker.",
    "actions": ["Restart the worker"],
    "recommendations": [
        {
            "type": "general",
            "priority": "medium",
            "title": "Add swap",
            "description": "The box has none.",
            "details": {},
            "actions": ["swapon"],
            "incident_id": 13,
        }
    ],
    "confidence": 0.8,
    "fallback_used": True,
    "errors": [],
}
NOTIFY_SNAPSHOT = {
    "deliveries": [
        {
            "driver": "felixlaan",
            "provider": "slack",
            "channel": "#alerts",
            "status": "success",
            "provider_id": "a970",
            "response": {"ok": True},
        },
        {"provider": "email", "channel": "ops@example.com", "status": "failed"},
    ],
    "provider_ids": ["a970"],
    "messages": [],
    "channels_attempted": 2,
    "channels_succeeded": 1,
    "channels_failed": 1,
    "errors": ["email: refused"],
}


def _raw(snapshot):
    return json.dumps(snapshot, indent=2, sort_keys=True, default=str, ensure_ascii=False)


def test_a_check_snapshot_lists_problems_worst_first_and_counts_the_rest():
    snapshot = {
        "checks": [
            {"name": "cpu", "status": "ok", "message": "fine", "metrics": {}},
            {"name": "memory", "status": "warning", "message": "Memory high", "metrics": {}},
            {"name": "raid", "status": "skipped", "message": "Skipped: no mdadm"},
            {"name": "disk", "status": "critical", "message": "Disk full", "metrics": {}},
            {"name": "network", "status": "unknown", "message": "ping failed"},
            {"name": "process", "status": "ok", "message": "fine"},
            {"name": "gpu", "status": "critical", "message": "GPU on fire\nfan stopped"},
        ],
        "checks_run": 7,
        "errors": ["disk: timed out", 5, "net: refused"],
    }

    body = stage_body("check", snapshot)

    assert body.kind == "check"
    assert [(d.headline, d.tone) for d in body.problems] == [
        (describe_check("disk", CheckStatus.CRITICAL, {}, "Disk full").headline, "critical"),
        ("GPU on fire", "critical"),
        ("Network reachability could not be checked", "muted"),
        (describe_check("memory", CheckStatus.WARNING, {}, "Memory high").headline, "warning"),
    ]
    gpu = body.problems[1]
    assert gpu.detail == "GPU on fire\nfan stopped"
    assert [(f.label, f.value) for f in gpu.facts] == [("Described by", "the node")]
    assert (body.ok_count, body.skipped_count) == (2, 1)
    assert body.errors.headline == "The checks hit 3 errors"
    assert [f.value for f in body.errors.facts] == ["disk: timed out", "5", "net: refused"]
    assert body.raw == _raw(snapshot)


def test_a_check_snapshot_without_errors_has_none():
    body = stage_body("check", {"checks": [], "errors": []})

    assert (body.problems, body.ok_count, body.skipped_count, body.errors) == ((), 0, 0, None)


def test_an_analyze_snapshot_becomes_its_outcome_and_recommendations():
    snapshot = {**ANALYZE_SNAPSHOT, "fallback_used": False, "failure": ""}

    body = stage_body("analyze", snapshot)

    assert body == AnalyzeBody(
        outcome=describe_analysis(snapshot),
        actions=("Restart the worker",),
        recommendations=(Recommendation("Add swap", "medium", "The box has none."),),
        raw=_raw(snapshot),
    )
    assert body.outcome.headline == "Memory is tight on web-1."
    assert body.kind == "analyze"


@pytest.mark.parametrize(
    "failure", [{"fallback_used": True}, {"fallback_used": False, "failure": "auth"}]
)
def test_a_failed_analysis_drops_its_recommendations_and_actions(failure):
    snapshot = {**ANALYZE_SNAPSHOT, **failure}

    body = stage_body("analyze", snapshot)

    assert body.outcome == describe_analysis(snapshot)
    assert body.outcome.tone == "warning"
    assert (body.actions, body.recommendations) == ((), ())
    assert not hasattr(body, "confidence")


def test_a_notify_snapshot_becomes_described_deliveries():
    body = stage_body("notify", NOTIFY_SNAPSHOT)

    assert body == NotifyBody(
        deliveries=describe_notify(NOTIFY_SNAPSHOT), raw=_raw(NOTIFY_SNAPSHOT)
    )
    assert [d.tone for d in body.deliveries] == ["ok", "critical"]
    assert body.kind == "notify"


@pytest.mark.parametrize("stage", ["check", "analyze", "notify", "ingest", "custom"])
@pytest.mark.parametrize("snapshot", [None, {}, [], "", 0])
def test_an_empty_snapshot_has_no_body(stage, snapshot):
    assert stage_body(stage, snapshot) is None


@pytest.mark.parametrize("stage", ["check", "analyze", "notify"])
@pytest.mark.parametrize("snapshot", [["a", {"b": 1}], "just text", 7, True])
def test_a_snapshot_that_is_not_an_object_is_shown_raw(stage, snapshot):
    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))
    assert stage_body(stage, snapshot).kind == "raw"


@pytest.mark.parametrize("stage", ["ingest", "custom"])
def test_a_stage_without_a_renderer_is_shown_raw(stage):
    snapshot = {"alerts_created": 1}

    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))


@pytest.mark.parametrize("stage", ["check", "analyze", "notify"])
def test_an_object_with_none_of_the_stage_keys_is_shown_raw(stage):
    snapshot = {"node_id": "n1", "output": {}, "skipped": True, "errors": ["boom"]}

    assert stage_body(stage, snapshot) == RawBody(raw=_raw(snapshot))


def test_a_malformed_check_snapshot_does_not_raise():
    snapshot = {
        "checks": [
            "cpu",
            None,
            {"name": ["x"], "status": {"a": 1}, "message": None, "metrics": [1]},
            {"name": 7, "status": "bogus", "message": 1.5},
            {"name": "cpu", "status": ["ok"], "metrics": "lots"},
            {},
        ],
        "checks_run": "two",
        "errors": "boom",
    }

    body = stage_body("check", snapshot)

    assert [(d.headline, d.tone) for d in body.problems] == [
        ("Unnamed check", "muted"),
        ("1.5", "muted"),
        ("CPU usage could not be checked", "muted"),
        ("Unnamed check", "muted"),
    ]
    assert (body.ok_count, body.skipped_count, body.errors) == (0, 0, None)
    assert stage_body("check", {"checks": {"name": "cpu"}}).problems == ()


def test_a_malformed_analyze_snapshot_does_not_raise():
    snapshot = {
        "summary": ["not", "text"],
        "probable_cause": {"a": 1},
        "actions": ["ok", 3, None, {"x": 1}, ""],
        "recommendations": ["text", {"title": None, "priority": 2, "description": []}],
        "fallback_used": "yes",
        "errors": [["nested"], "real"],
    }

    assert stage_body("analyze", snapshot) == AnalyzeBody(
        outcome=describe_analysis(snapshot),
        actions=("ok", "3"),
        recommendations=(Recommendation("", "2", ""),),
        raw=_raw(snapshot),
    )
    assert stage_body("analyze", {"actions": "restart", "recommendations": 5}).actions == ()


def test_a_malformed_notify_snapshot_does_not_raise():
    snapshot = {
        "deliveries": [None, "slack", {"provider": 1, "status": None, "provider_id": 42}],
        "channels_attempted": "1",
        "errors": {"a": "b"},
    }

    body = stage_body("notify", snapshot)

    assert [d.tone for d in body.deliveries] == ["critical"]
    assert stage_body("notify", {"deliveries": "none"}).deliveries == (
        Description("Nothing was sent", "muted"),
    )


def test_raw_json_keeps_non_ascii_text_readable():
    assert stage_body("ingest", {"note": "Disk doldu, çöp topla"}).raw == (
        '{\n  "note": "Disk doldu, çöp topla"\n}'
    )
