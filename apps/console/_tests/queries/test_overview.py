from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.queries import overview
from config.dashboard import NODE_RECENT_MINUTES


def _incident(title, severity="warning", status="open", age_hours=0, created_at=None):
    incident = Incident.objects.create(subject=title, severity=severity, status=status)
    Incident.objects.filter(pk=incident.pk).update(
        created_at=created_at or timezone.now() - timedelta(hours=age_hours)
    )
    return incident


def _subjects(rows):
    """Which incidents the rows hold, named by subject since titles are described on read."""
    found = Incident.objects.in_bulk([row.pk for row in rows])
    return [found[row.pk].subject for row in rows]


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

    # Incident rows, their hosts, then their alerts for the describer.
    with django_assert_num_queries(3):
        rows = overview.open_incidents()

    assert _subjects(rows) == ["critical", "old warning", "new warning", "info"]


@pytest.mark.django_db
def test_incident_row_carries_tones_count_and_a_link():
    incident = _incident("disk full", "critical")
    node = Node.objects.create(instance_id="n1", hostname="n1")
    _alert(node, incident=incident, key="a")
    _alert(node, incident=incident, key="b")

    other = _incident("swap low", "warning")
    _alert(node, incident=other, key="c")
    _incident("quiet", "info")

    row, other_row, quiet_row = overview.open_incidents()

    assert row.severity_tone == "critical"
    assert row.status_tone == "critical"
    assert row.alert_count == 2
    assert row.url == reverse("console:incident", args=[incident.pk])
    assert (row.pk, other_row.pk) == (incident.pk, other.pk)
    assert (other_row.title, other_row.alert_count) == ("c", 1)
    assert other_row.url == reverse("console:incident", args=[other.pk])
    assert (quiet_row.title, quiet_row.alert_count) == (f"Incident #{quiet_row.pk}", 0)


@pytest.mark.django_db
def test_open_incidents_cap_keeps_the_highest_ranked_rows(monkeypatch):
    monkeypatch.setattr(overview, "INCIDENT_LIMIT", 2)
    _incident("old warning", "warning", age_hours=5)
    _incident("new warning", "warning", age_hours=1)
    _incident("critical", "critical")

    rows = overview.open_incidents()

    assert _subjects(rows) == ["critical", "old warning"]


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
    assert tile.label == (f"{firing} firing" if firing else "healthy")
    assert tile.pk == node.pk
    assert tile.url == reverse("admin:alerts_node_change", args=[node.pk])
    assert tile.last_seen == Node.objects.get(pk=node.pk).last_seen


FROZEN = datetime(2026, 10, 11, 12, 30, tzinfo=dt_timezone.utc)
STALE = timedelta(minutes=NODE_RECENT_MINUTES)


def _node_seen(monkeypatch, ago, instance_id="n1", local_id="this-hub"):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)
    monkeypatch.setattr(overview, "local_instance_id", lambda: local_id)
    node = Node.objects.create(instance_id=instance_id, hostname="web-1")
    Node.objects.filter(pk=node.pk).update(last_seen=FROZEN - ago)
    return node


@pytest.mark.django_db
def test_a_silent_node_with_nothing_firing_is_quiet(monkeypatch):
    _node_seen(monkeypatch, timedelta(days=3))

    (tile,) = overview.node_tiles()

    assert (tile.tone, tile.label, tile.firing) == ("muted", "quiet", 0)


@pytest.mark.django_db
def test_a_fresh_node_with_nothing_firing_is_healthy(monkeypatch):
    _node_seen(monkeypatch, timedelta(minutes=1))

    (tile,) = overview.node_tiles()

    assert (tile.tone, tile.label, tile.firing) == ("ok", "healthy", 0)


@pytest.mark.django_db
def test_a_silent_node_with_firing_alerts_keeps_its_firing_tone_and_label(monkeypatch):
    node = _node_seen(monkeypatch, timedelta(days=3))
    _alert(node, severity="critical", key="a")
    _alert(node, severity="warning", key="b")

    (tile,) = overview.node_tiles()

    assert (tile.tone, tile.label, tile.firing) == ("critical", "2 firing", 2)


@pytest.mark.django_db
def test_this_instances_own_node_is_never_quiet(monkeypatch):
    _node_seen(monkeypatch, timedelta(days=3), instance_id="this-hub", local_id="this-hub")

    (tile,) = overview.node_tiles()

    assert (tile.tone, tile.label) == ("ok", "healthy")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("ago", "tone", "label"),
    [
        (STALE, "ok", "healthy"),
        (STALE + timedelta(microseconds=1), "muted", "quiet"),
    ],
    ids=["exactly-at-the-boundary", "just-past-it"],
)
def test_a_node_is_quiet_only_once_it_is_older_than_the_window(monkeypatch, ago, tone, label):
    _node_seen(monkeypatch, ago)

    (tile,) = overview.node_tiles()

    assert (tile.tone, tile.label) == (tone, label)


@pytest.mark.django_db
def test_node_tiles_read_the_clock_once(monkeypatch):
    calls = []
    monkeypatch.setattr(timezone, "now", lambda: calls.append(1) or FROZEN)
    Node.objects.create(instance_id="n1", hostname="a")
    Node.objects.create(instance_id="n2", hostname="b")
    calls.clear()

    overview.node_tiles()

    assert len(calls) == 1


@pytest.mark.django_db
def test_each_node_tile_counts_only_its_own_firing_alerts():
    loud = Node.objects.create(instance_id="n1", hostname="loud")
    mild = Node.objects.create(instance_id="n2", hostname="mild")
    Node.objects.create(instance_id="n3", hostname="quiet")
    _alert(loud, severity="critical", key="a")
    _alert(loud, severity="critical", key="b")
    _alert(loud, severity="warning", key="c")
    _alert(loud, severity="critical", status="resolved", key="d")
    _alert(mild, severity="info", key="a")
    _alert(mild, severity="warning", status="resolved", key="b")

    tiles = overview.node_tiles()

    assert [(tile.name, tile.tone, tile.firing) for tile in tiles] == [
        ("loud", "critical", 3),
        ("mild", "info", 1),
        ("quiet", "ok", 0),
    ]
    assert [tile.url for tile in tiles[:2]] == [
        reverse("admin:alerts_node_change", args=[loud.pk]),
        reverse("admin:alerts_node_change", args=[mild.pk]),
    ]


@pytest.mark.django_db
def test_node_tiles_cost_two_queries_and_fall_back_to_the_instance_id(django_assert_num_queries):
    Node.objects.create(instance_id="b-id", hostname="")
    alpha = Node.objects.create(instance_id="a-id", hostname="alpha")
    _alert(alpha, severity="critical", key="a")
    _alert(alpha, severity="warning", key="b")

    with django_assert_num_queries(2):
        tiles = overview.node_tiles()

    assert [tile.name for tile in tiles] == ["alpha", "b-id"]


@pytest.mark.django_db
def test_node_tiles_sort_by_name_ignoring_case():
    Node.objects.create(instance_id="n1", hostname="Zeta")
    Node.objects.create(instance_id="n2", hostname="alpha")
    Node.objects.create(instance_id="n3", hostname="Beta")

    assert [tile.name for tile in overview.node_tiles()] == ["alpha", "Beta", "Zeta"]


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
    this_hour = frozen.replace(minute=0)
    first = this_hour - timedelta(hours=overview.SPARK_HOURS - 1)
    _incident("on the first bucket", created_at=first)
    _incident("on the current hour", created_at=this_hour)
    _incident("a second too old", created_at=first - timedelta(seconds=1))

    with django_assert_num_queries(1):
        points = overview.incident_spark()

    assert len(points) == overview.SPARK_HOURS
    assert [x for x, _ in points] == list(range(overview.SPARK_HOURS))
    assert points[0][1] == 1
    assert points[-1][1] == 3
    assert points[-4][1] == 1
    assert sum(y for _, y in points) == 5


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

    assert [(item.key, item.label, item.tone, item.detail, item.url) for item in items] == [
        ("b", "Inbox", "warning", "3 stuck", "/b"),
        ("c", "Provider", "critical", "none", "/c"),
    ]


@pytest.mark.django_db
def test_build_overview_assembles_every_part():
    _incident("disk full", "critical")
    Node.objects.create(instance_id="n1", hostname="n1")

    result = overview.build_overview()

    assert result.headline == "1 open incident, 1 critical."
    assert _subjects(result.incidents) == ["disk full"]
    assert [tile.name for tile in result.nodes] == ["n1"]
    assert len(result.spark) == overview.SPARK_HOURS
    assert isinstance(result.attention, tuple)
    assert (result.open_total, result.open_hidden) == (1, 0)


@pytest.mark.django_db
def test_build_overview_counts_the_incidents_past_the_cap(monkeypatch, django_assert_num_queries):
    monkeypatch.setattr(overview, "INCIDENT_LIMIT", 2)
    monkeypatch.setattr(overview, "build_readiness", lambda: [])
    for index in range(5):
        _incident(f"incident {index}")
    _incident("resolved", status="resolved")

    # Counts, incident rows, their hosts, their alerts, firing alerts, nodes, sparkline.
    with django_assert_num_queries(7):
        result = overview.build_overview()

    assert len(result.incidents) == 2
    assert (result.open_total, result.open_hidden) == (5, 3)
    assert result.more_url == reverse("console:incidents")


def test_hour_bars_scale_to_the_busiest_hour_and_keep_a_stub_for_empty_hours():
    first = datetime(2026, 10, 14, 0, tzinfo=dt_timezone.utc)
    spark = ((0.0, 0.0), (1.0, 4.0), (2.0, 1.0))

    bars = overview.hour_bars(spark, first)

    assert [(bar.count, bar.x, bar.height, bar.y) for bar in bars] == [
        (0, 0, 1, 39),
        (4, 10, 40, 0),
        (1, 20, 10, 30),
    ]
    assert [bar.hour.hour for bar in bars] == [0, 1, 2]


def test_hour_bars_for_an_empty_window_are_all_stubs():
    first = datetime(2026, 10, 14, 0, tzinfo=dt_timezone.utc)

    bars = overview.hour_bars(((0.0, 0.0), (1.0, 0.0)), first)

    assert {bar.height for bar in bars} == {1}


@pytest.mark.django_db
def test_the_overview_knows_how_many_opened_and_when_its_window_starts(monkeypatch):
    now = datetime(2026, 10, 14, 15, 40, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(overview.timezone, "now", lambda: now)
    Incident.objects.create()
    Incident.objects.create()

    result = overview.build_overview()

    assert result.opened_total == 2
    assert result.spark_from == datetime(2026, 10, 13, 16, tzinfo=dt_timezone.utc)
    assert len(result.bars) == overview.SPARK_HOURS
