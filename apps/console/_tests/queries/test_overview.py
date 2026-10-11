from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.queries import overview


def _incident(title, severity="warning", status="open", age_hours=0):
    incident = Incident.objects.create(title=title, severity=severity, status=status)
    Incident.objects.filter(pk=incident.pk).update(
        created_at=timezone.now() - timedelta(hours=age_hours)
    )
    return incident


def _alert(node, severity="warning", status="firing", incident=None, key="a"):
    return Alert.objects.create(
        fingerprint=f"{node.instance_id}:{key}",
        name=key,
        severity=severity,
        status=status,
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )


@pytest.mark.parametrize(
    ("total", "critical", "expected"),
    [
        (0, 0, "No open incidents."),
        (1, 0, "1 open incident."),
        (3, 0, "3 open incidents."),
        (3, 2, "3 open incidents, 2 critical."),
    ],
)
def test_headline_reads_as_a_sentence(total, critical, expected):
    assert overview.headline(total, critical) == expected


@pytest.mark.django_db
def test_open_incidents_sort_by_severity_then_oldest_first(django_assert_num_queries):
    _incident("new warning", "warning", age_hours=1)
    _incident("old warning", "warning", age_hours=5)
    _incident("info", "info", age_hours=9)
    _incident("critical", "critical", status="acknowledged")
    _incident("resolved", "critical", status="resolved")
    _incident("closed", "critical", status="closed")

    with django_assert_num_queries(1):
        rows = overview.open_incidents()

    assert [row.title for row in rows] == ["critical", "old warning", "new warning", "info"]


@pytest.mark.django_db
def test_incident_row_carries_tones_count_and_a_link():
    incident = _incident("disk full", "critical")
    node = Node.objects.create(instance_id="n1", hostname="n1")
    _alert(node, incident=incident, key="a")
    _alert(node, incident=incident, key="b")

    (row,) = overview.open_incidents()

    assert row.severity_tone == "critical"
    assert row.status_tone == "critical"
    assert row.alert_count == 2
    assert row.url == reverse("admin:alerts_incident_change", args=[incident.pk])


@pytest.mark.django_db
def test_open_incidents_are_capped(monkeypatch):
    monkeypatch.setattr(overview, "INCIDENT_LIMIT", 2)
    for n in range(3):
        _incident(f"i{n}")

    assert len(overview.open_incidents()) == 2


@pytest.mark.django_db
def test_open_counts_ignore_the_cap(django_assert_num_queries):
    _incident("a", "critical")
    _incident("b", "warning")
    _incident("c", "critical", status="resolved")

    with django_assert_num_queries(1):
        counts = overview.open_counts()

    assert counts == (2, 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("alerts", "tone", "firing"),
    [
        ([], "ok", 0),
        ([("info", "firing")], "info", 1),
        ([("warning", "firing"), ("info", "firing")], "warning", 2),
        ([("critical", "firing"), ("warning", "firing")], "critical", 2),
        ([("critical", "resolved")], "ok", 0),
    ],
)
def test_node_tile_takes_the_worst_firing_tone(alerts, tone, firing):
    node = Node.objects.create(instance_id="n1", hostname="web-1")
    for index, (severity, status) in enumerate(alerts):
        _alert(node, severity=severity, status=status, key=str(index))

    (tile,) = overview.node_tiles()

    assert (tile.name, tile.tone, tile.firing) == ("web-1", tone, firing)
    assert tile.url == reverse("admin:alerts_node_change", args=[node.pk])


@pytest.mark.django_db
def test_node_tiles_cost_one_query_and_fall_back_to_the_instance_id(django_assert_num_queries):
    Node.objects.create(instance_id="b-id", hostname="")
    Node.objects.create(instance_id="a-id", hostname="alpha")

    with django_assert_num_queries(1):
        tiles = overview.node_tiles()

    assert [tile.name for tile in tiles] == ["alpha", "b-id"]


@pytest.mark.django_db
def test_spark_has_one_point_per_hour_and_counts_into_the_right_bucket(
    django_assert_num_queries, monkeypatch
):
    # One instant for the rows and the buckets, or a run across an hour boundary splits them.
    frozen = datetime(2026, 10, 11, 12, 30, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: frozen)
    _incident("now")
    _incident("also now")
    _incident("three hours ago", age_hours=3)
    _incident("too old", age_hours=30)

    with django_assert_num_queries(1):
        points = overview.incident_spark()

    assert len(points) == overview.SPARK_HOURS
    assert [x for x, _ in points] == list(range(overview.SPARK_HOURS))
    assert points[-1][1] == 2
    assert points[-4][1] == 1
    assert sum(y for _, y in points) == 3


def test_attention_keeps_only_warn_and_error_and_maps_them_to_tones(monkeypatch):
    monkeypatch.setattr(
        overview,
        "build_readiness",
        lambda: [
            {"key": "a", "label": "Channels", "status": "ok", "detail": "1 active", "url": "/a"},
            {"key": "b", "label": "Inbox", "status": "warn", "detail": "3 stuck", "url": "/b"},
            {"key": "c", "label": "Provider", "status": "error", "detail": "none", "url": "/c"},
            {"key": "d", "label": "Nodes", "status": "info", "detail": "Standalone", "url": "/d"},
        ],
    )

    items = overview.attention()

    assert [(item.label, item.tone, item.detail, item.url) for item in items] == [
        ("Inbox", "warning", "3 stuck", "/b"),
        ("Provider", "critical", "none", "/c"),
    ]


@pytest.mark.django_db
def test_build_overview_assembles_every_part():
    _incident("disk full", "critical")
    Node.objects.create(instance_id="n1", hostname="n1")

    result = overview.build_overview()

    assert result.headline == "1 open incident, 1 critical."
    assert [row.title for row in result.incidents] == ["disk full"]
    assert [tile.name for tile in result.nodes] == ["n1"]
    assert len(result.spark) == overview.SPARK_HOURS
    assert isinstance(result.attention, tuple)
