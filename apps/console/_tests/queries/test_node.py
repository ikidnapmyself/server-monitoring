import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.alerts.node_overview import PEER_HISTORY_NOTE, PEER_PREFLIGHT_NOTE
from apps.checkers.checkers import checker_label
from apps.checkers.models import CheckRun, PreflightRun
from apps.console._tests.queries.test_runs import _run
from apps.console.queries.node import build_node_detail
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db

POLICY = {
    "cpu": {"warning_threshold": 80, "critical_threshold": 90, "warn_level": 3},
    "disk_inodes": {"warning_threshold": 70},
}


def _hub():
    return Node.objects.create(instance_id=local_instance_id(), hostname="hub")


def _peer(**fields):
    return Node.objects.create(**{"instance_id": "web-1", "hostname": "web-1", **fields})


def _alert(node, key, incident=None, severity="warning", **fields):
    return Alert.objects.create(
        fingerprint=f"fp-{key}",
        name=key,
        severity=severity,
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
        **fields,
    )


def test_a_missing_node_is_none():
    assert build_node_detail(999) is None


def test_the_hub_reads_its_checks_charts_and_preflight():
    node = _hub()
    for value in (10.0, 20.0, 30.0):
        CheckRun.objects.create(
            checker_name="cpu", hostname="hub", status="ok", metrics={"cpu_percent": value}
        )
    run = PreflightRun.objects.create(
        instance_id=local_instance_id(), passed=5, warnings=1, errors=0, overall_status="warning"
    )

    detail = build_node_detail(node.pk)

    assert detail.role == "This hub"
    assert detail.freshness_tone == "muted"
    assert detail.freshness.startswith("self-check ")
    assert detail.checkers[0].label == checker_label("cpu")
    assert detail.checkers[0].key == "cpu"
    assert detail.checker_time_label == "Observed"
    assert [chart.title for chart in detail.charts] == ["CPU"]
    assert detail.charts_note == ""
    assert detail.preflight == "5 passed, 1 warning, 0 errors."
    assert detail.preflight_at == run.created_at
    assert detail.preflight_action == Action(
        "Open the preflight run", "view_preflight_run", {"pk": run.pk}
    )
    assert detail.preflight_note == ""


def test_the_hub_with_no_preflight_says_so():
    detail = build_node_detail(_hub().pk)

    assert (detail.preflight, detail.preflight_at, detail.preflight_action) == ("", None, None)
    assert detail.preflight_note == "No preflight recorded on this machine yet."


def test_a_peer_reads_its_alerts_and_has_no_charts():
    node = _peer()
    alert = _alert(node, "cpu", labels={"checker": "cpu"})

    detail = build_node_detail(node.pk)

    assert detail.role == "Peer"
    assert detail.freshness.startswith("seen ")
    assert detail.freshness_tone == "ok"
    assert detail.checkers[0].action == Action(
        "Open the alert", intent="view_alert", params={"pk": alert.pk}
    )
    assert detail.charts == ()
    assert detail.charts_note == PEER_HISTORY_NOTE
    assert detail.preflight == ""
    assert detail.preflight_note == PEER_PREFLIGHT_NOTE


def test_a_silent_peer_reads_quiet_and_amber():
    node = _peer()
    Node.objects.filter(pk=node.pk).update(last_seen=timezone.now() - timedelta(hours=2))

    detail = build_node_detail(node.pk)

    assert (detail.tone, detail.label) == ("muted", "quiet")
    assert detail.freshness_tone == "warning"


def test_an_unnamed_node_keeps_its_id_for_details():
    node = _peer(instance_id="0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11", hostname="")

    detail = build_node_detail(node.pk)

    assert detail.name == "Unnamed node"
    assert Fact("Instance ID", node.instance_id, copy=True) in detail.facts


def test_facts_name_only_what_is_set():
    node = _peer(address="10.0.0.5", last_source="cluster")

    facts = build_node_detail(node.pk).facts

    assert facts == (
        Fact("Instance ID", "web-1", copy=True),
        Fact("Address", "10.0.0.5"),
        Fact("Last source", "cluster"),
    )
    assert build_node_detail(_peer(instance_id="db-1").pk).facts == (
        Fact("Instance ID", "db-1", copy=True),
    )


def test_open_incidents_are_cards_worst_first_and_match_the_counts():
    node = _peer()
    warning = Incident.objects.create(severity="warning", status="open")
    critical = Incident.objects.create(severity="critical", status="open")
    info = Incident.objects.create(severity="info", status="acknowledged")
    resolved = Incident.objects.create(severity="critical", status="resolved")
    for key, incident in (("w", warning), ("c", critical), ("i", info), ("r", resolved)):
        _alert(node, key, incident=incident)

    detail = build_node_detail(node.pk)

    assert [card.pk for card in detail.incidents] == [critical.pk, warning.pk, info.pk]
    assert detail.incidents[0].url == f"/console/incidents/{critical.pk}/"
    assert sum(count.count for count in detail.counts) == 3


def test_recent_runs_are_the_runs_list_rows_for_this_node():
    node = _peer()
    runs = [_run(node=node) for _ in range(12)]

    detail = build_node_detail(node.pk)

    assert [row.pk for row in detail.runs] == [run.pk for run in reversed(runs)][:10]
    assert detail.runs_url == f"/console/runs/?node={node.pk}"


def test_the_policy_reads_in_effect_not_scoring_and_unread():
    node = _peer(config=POLICY)

    detail = build_node_detail(node.pk)

    (cpu,) = detail.in_effect
    assert cpu.key == "cpu"
    assert cpu.label == checker_label("cpu")
    assert cpu.values == ("Warning at 80", "Critical at 90")
    assert cpu.note == ""
    (inodes,) = detail.not_scoring
    assert inodes.note == "Set a critical threshold too, or clear both."
    assert detail.unread == (Fact(checker_label("cpu"), "cpu → warn_level"),)


@pytest.mark.parametrize("can_change", [True, False])
def test_only_a_changer_gets_actions(can_change):
    node = _peer(config=POLICY)
    pk = node.pk

    detail = build_node_detail(pk, can_change=can_change)

    if can_change:
        assert detail.do == (
            Action("Edit policy", "edit_node_policy", {"pk": pk}),
            Action("Re-evaluate open alerts", "reevaluate_node", {"pk": pk}),
        )
        assert detail.in_effect[0].edit == Action(
            "Edit", "edit_node_policy", {"pk": pk, "checker": "cpu"}
        )
    else:
        assert detail.do == ()
        assert detail.in_effect[0].edit is None


def test_raw_holds_labels_and_config():
    node = _peer(labels={"role": "web"}, config=POLICY)

    detail = build_node_detail(node.pk)

    assert json.loads(detail.raw) == {"labels": {"role": "web"}, "config": POLICY}
    assert detail.url == f"/console/nodes/{node.pk}/"
