import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.alerts.node_overview import PEER_HISTORY_NOTE, PEER_PREFLIGHT_NOTE
from apps.checkers.checkers import checker_label
from apps.checkers.models import CheckRun, PreflightRun
from apps.console._tests.queries.test_runs import _run
from apps.console.queries.node import build_node_detail
from apps.console.queries.nodes import open_incidents_by_node
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
    assert detail.incidents[0].link.params == {"pk": critical.pk}
    assert detail.incidents[0].link.intent == "view_incident"
    assert sum(count.count for count in detail.counts) == 3


def test_open_incidents_of_one_severity_sort_by_their_latest_fire():
    node = _peer()
    now = timezone.now()
    reopened = Incident.objects.create(severity="warning", status="open")
    fresh = Incident.objects.create(severity="warning", status="open")
    Incident.objects.filter(pk=reopened.pk).update(
        created_at=now - timedelta(days=30), opened_at=now
    )
    Incident.objects.filter(pk=fresh.pk).update(
        created_at=now - timedelta(hours=1), opened_at=now - timedelta(hours=1)
    )
    _alert(node, "r", incident=reopened)
    _alert(node, "f", incident=fresh)

    detail = build_node_detail(node.pk)

    assert [card.pk for card in detail.incidents] == [fresh.pk, reopened.pk]


def test_recent_runs_are_the_runs_list_rows_for_this_node():
    node = _peer()
    runs = [_run(node=node) for _ in range(12)]

    detail = build_node_detail(node.pk)

    assert [row.pk for row in detail.runs] == [run.pk for run in reversed(runs)][:10]
    assert detail.all_runs == Action("All runs on this node", "view_runs", {"node": node.pk})


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


def test_the_node_offers_its_actions():
    node = _peer(config=POLICY)
    pk = node.pk

    detail = build_node_detail(pk)

    assert detail.do == (
        Action("Edit policy", "edit_node_policy", {"pk": pk}),
        Action("Re-evaluate open alerts", "reevaluate_node", {"pk": pk}),
    )
    assert detail.in_effect[0].edit == Action(
        "Edit", "edit_node_policy", {"pk": pk, "checker": "cpu"}
    )


def test_raw_holds_labels_and_config():
    node = _peer(labels={"role": "web"}, config=POLICY)

    detail = build_node_detail(node.pk)

    assert json.loads(detail.raw) == {"labels": {"role": "web"}, "config": POLICY}
    assert detail.url == f"/console/nodes/{node.pk}/"


def test_the_query_count_does_not_grow_with_incidents_runs_or_other_nodes():
    node = _peer()

    def queries(total):
        for index in range(total):
            incident = Incident.objects.create(severity="critical", status="open")
            _alert(node, f"{total}-{index}", incident=incident)
            _run(node=node, incident=incident)
            other = _peer(instance_id=f"other-{total}-{index}", hostname=f"other-{total}-{index}")
            _alert(other, f"other-{total}-{index}", incident=incident)
        with CaptureQueriesContext(connection) as captured:
            build_node_detail(node.pk)
        return len(captured)

    assert queries(2) == queries(12)


def test_the_incident_scan_reads_only_this_nodes_alerts():
    node = _peer()
    other = _peer(instance_id="db-1", hostname="db-1")
    mine = Incident.objects.create(severity="warning", status="open")
    theirs = Incident.objects.create(severity="critical", status="open")
    _alert(node, "mine", incident=mine)
    _alert(None, "labelled", incident=mine, labels={"hostname": "web-1"})
    _alert(other, "theirs", incident=theirs)

    with CaptureQueriesContext(connection) as captured:
        found = open_incidents_by_node([node])

    assert found == {node.pk: {"warning": {mine.pk}}}
    scan = next(q["sql"] for q in captured.captured_queries if "labels" in q["sql"])
    assert "node_id" in scan.split("WHERE", 1)[1]


def _mixed_hub():
    node = _hub()
    for name, status in (
        ("cpu", "ok"),
        ("disk", "warning"),
        ("memory", "critical"),
        ("network", "unknown"),
        ("process", "ok"),
        ("raid", "skipped"),
    ):
        CheckRun.objects.create(checker_name=name, hostname="hub", status=status, metrics={})
    return node


def test_each_checker_status_reads_its_label_and_tone():
    detail = build_node_detail(_mixed_hub().pk)

    by_key = {line.key: (line.status_label, line.tone) for line in detail.checkers}
    assert by_key == {
        "memory": ("Critical", "critical"),
        "disk": ("Warning", "warning"),
        "network": ("Unknown", "muted"),
        "cpu": ("OK", "ok"),
        "process": ("OK", "ok"),
        "raid": ("Skipped", "muted"),
    }


def test_an_unmapped_status_keeps_its_word_and_reads_muted():
    node = _hub()
    CheckRun.objects.create(checker_name="cpu", hostname="hub", status="odd_one", metrics={})

    line = build_node_detail(node.pk).checkers[0]

    assert (line.status_label, line.tone) == ("Odd one", "muted")


def test_checkers_come_out_worst_first_then_by_label():
    detail = build_node_detail(_mixed_hub().pk)

    assert [line.key for line in detail.checkers] == [
        "memory",
        "disk",
        "network",
        *sorted(["cpu", "process"], key=checker_label),
        "raid",
    ]


@pytest.mark.parametrize(
    "value, keys",
    [
        ("problems", {"memory", "disk", "network"}),
        ("ok", {"cpu", "process"}),
        ("", {"memory", "disk", "network", "cpu", "process", "raid"}),
        ("bogus", {"memory", "disk", "network", "cpu", "process", "raid"}),
    ],
)
def test_the_checker_filter_keeps_only_its_rows(value, keys):
    detail = build_node_detail(_mixed_hub().pk, checkers=value)

    assert {line.key for line in detail.checkers} == keys


@pytest.mark.parametrize(
    "value, active, url",
    [("problems", "problems", "?checkers=problems"), ("ok", "ok", "?checkers=ok"), ("x", "", "")],
)
def test_the_checker_chips_count_every_row_and_mark_the_active_one(value, active, url):
    node = _mixed_hub()
    base = f"/console/nodes/{node.pk}/"

    detail = build_node_detail(node.pk, checkers=value)

    assert [(c.key, c.label, c.count, c.url) for c in detail.checker_chips] == [
        ("all", "All", 6, base),
        ("problems", "Problems", 3, f"{base}?checkers=problems"),
        ("ok", "OK", 2, f"{base}?checkers=ok"),
    ]
    assert [c.key for c in detail.checker_chips if c.active] == [active or "all"]
    assert detail.url == base + url


def test_a_node_with_no_checkers_has_no_chips():
    assert build_node_detail(_peer().pk).checker_chips == ()
