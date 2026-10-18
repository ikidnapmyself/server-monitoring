from datetime import timedelta

import pytest
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.alerts.identity import local_instance_id
from apps.alerts.models import Alert, Incident, Node
from apps.console.filters import IncidentFilter, NodeFilter
from apps.console.queries import overview
from apps.console.queries.incidents import build_incident_list
from apps.console.queries.nodes import build_node_list
from config.describing import Action

pytestmark = pytest.mark.django_db


def _node(name, instance_id=None, ago=None):
    node = Node.objects.create(instance_id=instance_id or f"id-{name}", hostname=name)
    if ago is not None:
        Node.objects.filter(pk=node.pk).update(last_seen=timezone.now() - ago)
    return node


def _alert(key, node=None, severity="warning", status="firing", incident=None, labels=None):
    return Alert.objects.create(
        fingerprint=f"fp-{key}",
        name=key,
        severity=severity,
        status=status,
        source="cluster",
        node=node,
        incident=incident,
        labels=labels or {},
        started_at=timezone.now(),
    )


def _incident(severity="warning", status="open"):
    return Incident.objects.create(severity=severity, status=status)


def test_rows_read_worst_first_then_by_name():
    loud = _node("loud")
    _alert("a", loud, severity="critical")
    mild = _node("mild")
    _alert("b", mild, severity="warning")
    _node("silent", ago=timedelta(hours=2))
    _node("b")
    _node("A")

    rows = build_node_list(NodeFilter()).rows

    assert [row.tone for row in rows] == ["critical", "warning", "muted", "ok", "ok"]
    assert [row.name for row in rows] == ["loud", "mild", "silent", "A", "b"]


def test_the_tone_and_label_follow_the_overview_tile():
    loud = _node("loud")
    _alert("a", loud, severity="critical")
    _alert("b", loud, severity="info")
    _node("silent", ago=timedelta(hours=2))
    _node("fine")

    tiles = {tile.pk: (tile.tone, tile.label) for tile in overview.node_tiles()}
    rows = build_node_list(NodeFilter()).rows

    assert {row.pk: (row.tone, row.label) for row in rows} == tiles


def test_the_hub_is_never_silent():
    hub = _node("hub", instance_id=local_instance_id(), ago=timedelta(days=3))
    peer = _node("peer", ago=timedelta(days=3))

    (row,) = (row for row in build_node_list(NodeFilter()).rows if row.pk == hub.pk)
    silent = build_node_list(NodeFilter(freshness="silent")).rows

    assert row.role == "This hub"
    assert [r.pk for r in silent] == [peer.pk]
    assert silent[0].role == "Peer"


def test_freshness_chips_count_and_narrow():
    _node("old", ago=timedelta(hours=2))
    _node("new-1")
    _node("new-2")

    found = build_node_list(NodeFilter(freshness="reporting"))

    (group,) = found.groups
    assert [(chip.key, chip.count, chip.active) for chip in group.chips] == [
        ("all", 3, False),
        ("silent", 1, False),
        ("reporting", 2, True),
    ]
    assert [chip.url for chip in group.chips] == [
        "/console/nodes/",
        "/console/nodes/?freshness=silent",
        "/console/nodes/?freshness=reporting",
    ]
    assert [row.name for row in found.rows] == ["new-1", "new-2"]
    assert found.url == "/console/nodes/?freshness=reporting"
    assert found.kept == (("freshness", "reporting"),)


def test_q_matches_hostname_or_instance_id():
    _node("web-1", instance_id="i-aaa")
    _node("db-1", instance_id="i-WEBBY")
    _node("cache", instance_id="i-ccc")

    found = build_node_list(NodeFilter(q="WeB"))

    assert [row.name for row in found.rows] == ["db-1", "web-1"]
    assert found.q == "WeB"


def test_counts_follow_the_incident_list_rule():
    node = _node("web-1", instance_id="i-web")
    other = _node("db-1")
    critical = _incident("critical")
    _alert("a", node, severity="critical", incident=critical)
    _alert("b", node, severity="critical", incident=critical)
    acked = _incident("critical", status="acknowledged")
    _alert("c", node, incident=acked)
    gone = _incident("critical", status="resolved")
    _alert("d", node, status="resolved", incident=gone)
    labelled = _incident("warning")
    _alert("e", labels={"instance_id": "i-web"}, incident=labelled)
    _alert("f", other, incident=_incident("info"))

    rows = {row.pk: row for row in build_node_list(NodeFilter()).rows}
    counts = rows[node.pk].counts

    assert [(c.severity, c.count, c.tone) for c in counts] == [
        ("critical", 2, "critical"),
        ("warning", 1, "warning"),
    ]
    for count in counts:
        assert count.url == f"/console/incidents/?severity={count.severity}&node={node.pk}"
        listed = build_incident_list(
            IncidentFilter.parse(QueryDict(count.url.split("?", 1)[1]))
        ).rows
        expected = {critical.pk, acked.pk} if count.severity == "critical" else {labelled.pk}
        assert {row.pk for row in listed} == expected


def test_a_node_with_nothing_open_has_no_counts():
    _node("web-1")

    (row,) = build_node_list(NodeFilter()).rows

    assert row.counts == ()


def test_an_unnamed_node_reads_unnamed_node():
    node = Node.objects.create(instance_id="0b9e6c1e-6f4a-4c43-9a59-5b3f1f2a7c11", hostname="")

    (row,) = build_node_list(NodeFilter()).rows

    assert row.name == "Unnamed node"
    assert row.url == f"/console/nodes/{node.pk}/"


def test_the_list_links_the_fleet_policy_and_names_a_dropped_filter():
    found = build_node_list(NodeFilter(dropped=("freshness",)))

    assert found.policy == Action("Fleet policy", intent="view_policy")
    assert found.notice == "Ignored invalid filter: freshness."
    assert build_node_list(NodeFilter()).notice == ""


def test_empty_states():
    assert build_node_list(NodeFilter()).empty == "none"
    _node("web-1")
    assert build_node_list(NodeFilter()).empty == ""
    assert build_node_list(NodeFilter(q="nothing")).empty == "no_match"


def test_the_query_count_does_not_grow_with_the_nodes():
    def queries(total):
        Node.objects.all().delete()
        for index in range(total):
            node = _node(f"n{total}-{index}")
            _alert(f"{total}-{index}", node, incident=_incident())
        with CaptureQueriesContext(connection) as captured:
            build_node_list(NodeFilter())
        return len(captured)

    assert queries(2) == queries(12)
