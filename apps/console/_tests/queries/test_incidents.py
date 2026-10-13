from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console import filters
from apps.console.filters import IncidentFilter
from apps.console.queries import incidents
from apps.console.queries.incidents import build_incident_list

FROZEN = datetime(2026, 10, 14, 12, 0, tzinfo=dt_timezone.utc)
ALL = IncidentFilter(status="all")


@pytest.fixture
def base():
    return reverse("console:incidents")


def _incident(title, severity="warning", status="open", age_hours=0.0, created_at=None):
    incident = Incident.objects.create(title=title, severity=severity, status=status)
    Incident.objects.filter(pk=incident.pk).update(
        created_at=created_at or timezone.now() - timedelta(hours=age_hours)
    )
    return incident


def _alert(incident, node=None, key="a"):
    return Alert.objects.create(
        fingerprint=f"{incident.pk}:{key}",
        name=key,
        severity="warning",
        status="firing",
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )


def _titles(result):
    return [row.title for row in result.rows]


def _group(result, name):
    (group,) = [group for group in result.groups if group.name == name]
    return group


@pytest.mark.django_db
def test_the_default_filter_lists_active_incidents_by_severity_then_newest_first():
    _incident("old warning", "warning", age_hours=5)
    _incident("new warning", "warning", age_hours=1)
    _incident("info", "info")
    _incident("critical", "critical", status="acknowledged", age_hours=9)
    _incident("resolved", "critical", status="resolved")
    _incident("closed", "critical", status="closed")

    result = build_incident_list(IncidentFilter())

    assert _titles(result) == ["critical", "new warning", "old warning", "info"]
    assert result.total == 4
    assert (result.page, result.pages) == (1, 1)
    assert (result.prev_url, result.next_url) == ("", "")
    assert (result.notice, result.empty) == ("", "")


@pytest.mark.django_db
def test_incidents_created_at_the_same_instant_keep_a_stable_order():
    first = _incident("first", created_at=FROZEN)
    second = _incident("second", created_at=FROZEN)

    assert [row.pk for row in build_incident_list(ALL).rows] == [second.pk, first.pk]


@pytest.mark.django_db
def test_a_row_carries_tones_count_and_the_admin_link():
    incident = _incident("disk full", "critical", status="acknowledged")
    _alert(incident, key="a")
    _alert(incident, key="b")
    quiet = _incident("quiet", "info", status="closed")

    row, quiet_row = build_incident_list(ALL).rows

    assert (row.pk, row.title) == (incident.pk, "disk full")
    assert (row.severity, row.severity_tone) == ("critical", "critical")
    assert (row.status, row.status_tone) == ("acknowledged", "warning")
    assert row.alert_count == 2
    assert row.created_at == Incident.objects.get(pk=incident.pk).created_at
    assert row.url == reverse("console:incident", args=[incident.pk])
    assert row.nodes == ""
    assert (quiet_row.pk, quiet_row.alert_count) == (quiet.pk, 0)
    assert (quiet_row.severity_tone, quiet_row.status_tone) == ("info", "muted")


@pytest.mark.django_db
def test_nodes_reads_as_the_first_name_plus_a_count_of_the_rest():
    zeta = Node.objects.create(instance_id="n1", hostname="zeta")
    beta = Node.objects.create(instance_id="n2", hostname="Beta")
    bare = Node.objects.create(instance_id="alpha-id", hostname="")
    three = _incident("three nodes", "critical")
    for index, node in enumerate([zeta, beta, bare, beta, None]):
        _alert(three, node, key=str(index))
    one = _incident("one node", "warning")
    _alert(one, zeta, key="a")
    _alert(one, zeta, key="b")
    none = _incident("no node", "info")
    _alert(none, None)

    rows = build_incident_list(ALL).rows

    assert [(row.title, row.nodes) for row in rows] == [
        ("three nodes", "alpha-id +2"),
        ("one node", "zeta"),
        ("no node", ""),
    ]
    assert [row.alert_count for row in rows] == [5, 2, 1]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", ["acked", "open"]),
        ("open", ["open"]),
        ("acknowledged", ["acked"]),
        ("resolved", ["resolved"]),
        ("closed", ["closed"]),
        ("all", ["closed", "resolved", "acked", "open"]),
    ],
)
def test_the_status_filter_narrows_the_rows(status, expected):
    _incident("open", age_hours=4)
    _incident("acked", status="acknowledged", age_hours=3)
    _incident("resolved", status="resolved", age_hours=2)
    _incident("closed", status="closed", age_hours=1)

    result = build_incident_list(IncidentFilter(status=status))

    assert _titles(result) == expected
    assert result.total == len(expected)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("", ["critical", "warning", "info"]),
        ("critical", ["critical"]),
        ("warning", ["warning"]),
        ("info", ["info"]),
    ],
)
def test_the_severity_filter_narrows_the_rows(severity, expected):
    for name in ("info", "warning", "critical"):
        _incident(name, name)

    assert _titles(build_incident_list(IncidentFilter(severity=severity))) == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("since", "expected"),
    [
        ("", ["now", "two days", "ten days", "forty days"]),
        ("24h", ["now"]),
        ("7d", ["now", "two days"]),
        ("30d", ["now", "two days", "ten days"]),
    ],
)
def test_the_since_filter_narrows_the_rows(monkeypatch, since, expected):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)
    _incident("now")
    _incident("two days", age_hours=48)
    _incident("ten days", age_hours=240)
    _incident("forty days", age_hours=960)

    assert _titles(build_incident_list(IncidentFilter(since=since))) == expected


@pytest.mark.django_db
def test_the_since_cutoff_is_inclusive(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)
    _incident("on the cutoff", created_at=FROZEN - timedelta(hours=24))
    _incident("just past it", created_at=FROZEN - timedelta(hours=24, seconds=1))

    assert _titles(build_incident_list(IncidentFilter(since="24h"))) == ["on the cutoff"]


@pytest.mark.django_db
def test_the_node_filter_narrows_the_rows_and_keeps_the_alert_count():
    web = Node.objects.create(instance_id="web-1", hostname="web-1")
    db = Node.objects.create(instance_id="db-1", hostname="db-1")
    shared = _incident("shared", "critical")
    _alert(shared, web, key="a")
    _alert(shared, web, key="b")
    _alert(shared, db, key="c")
    _alert(shared, None, key="d")
    other = _incident("db only", "warning")
    _alert(other, db)
    _incident("no alerts", "info")

    result = build_incident_list(IncidentFilter(node=web.pk))

    assert [(row.title, row.alert_count, row.nodes) for row in result.rows] == [
        ("shared", 4, "db-1 +1")
    ]
    assert result.total == 1
    assert _titles(build_incident_list(IncidentFilter(node=db.pk))) == ["shared", "db only"]


@pytest.mark.django_db
def test_filters_combine(monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: FROZEN)
    node = Node.objects.create(instance_id="web-1")
    match = _incident("match", "critical", status="resolved")
    _alert(match, node)
    _incident("wrong node", "critical", status="resolved")
    wrong_severity = _incident("wrong severity", "info", status="resolved")
    _alert(wrong_severity, node)
    wrong_status = _incident("wrong status", "critical")
    _alert(wrong_status, node)
    too_old = _incident("too old", "critical", status="resolved", age_hours=200)
    _alert(too_old, node)

    result = build_incident_list(
        IncidentFilter(status="resolved", severity="critical", node=node.pk, since="7d")
    )

    assert _titles(result) == ["match"]


@pytest.mark.django_db
def test_a_node_that_does_not_exist_is_an_invalid_filter(base):
    node = Node.objects.create(instance_id="web-1")
    _incident("kept")

    result = build_incident_list(IncidentFilter(severity="warning", node=node.pk + 1000))

    assert _titles(result) == ["kept"]
    assert result.notice == "Ignored invalid filter: node."
    assert result.url == f"{base}?severity=warning"
    assert [chip.key for chip in _group(result, "node").chips if chip.active] == ["any"]


@pytest.mark.django_db
def test_a_node_filter_with_no_nodes_at_all_is_dropped_too():
    result = build_incident_list(IncidentFilter(node=1))

    assert result.notice == "Ignored invalid filter: node."
    assert [group.name for group in result.groups] == ["status", "severity", "since"]


@pytest.mark.django_db
def test_the_notice_names_every_dropped_param():
    result = build_incident_list(IncidentFilter(node=5, dropped=("status", "page")))

    assert result.notice == "Ignored invalid filter: status, node, page."


@pytest.mark.django_db
def test_no_incident_at_all_reads_as_none(base):
    result = build_incident_list(IncidentFilter(severity="critical"))

    assert result.empty == "none"
    assert (result.rows, result.total, result.page, result.pages) == ((), 0, 1, 1)
    assert result.url == f"{base}?severity=critical"


@pytest.mark.django_db
def test_incidents_the_filter_hides_read_as_no_match():
    _incident("resolved", status="resolved")

    result = build_incident_list(IncidentFilter(severity="info"))

    assert result.empty == "no_match"
    assert (result.rows, result.total) == ((), 0)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "default",
    [IncidentFilter(), IncidentFilter(page=4), IncidentFilter(node=77, dropped=("since",))],
    ids=["bare", "page-clamped", "only-dropped-params"],
)
def test_default_filters_with_only_finished_incidents_read_as_none_active(default):
    _incident("resolved", status="resolved")
    _incident("closed", status="closed")

    result = build_incident_list(default)

    assert result.empty == "none_active"
    assert (result.rows, result.total) == ((), 0)


def test_every_value_the_filter_accepts_has_a_chip():
    def values(choices):
        return [value for _, _, value in choices]

    assert values(incidents.STATUS_CHIPS) == list(filters.STATUS_CHOICES)
    assert values(incidents.SEVERITY_CHIPS) == list(filters.SEVERITY_CHOICES)
    assert values(incidents.SINCE_CHIPS) == list(filters.SINCE_CHOICES)


@pytest.mark.django_db
def test_pages_hold_page_size_rows_and_link_to_their_neighbours(monkeypatch, base):
    monkeypatch.setattr(incidents, "PAGE_SIZE", 2)
    for index in range(5):
        _incident(f"incident {index}", age_hours=index)

    first = build_incident_list(IncidentFilter())
    second = build_incident_list(IncidentFilter(page=2))
    third = build_incident_list(IncidentFilter(page=3))

    assert _titles(first) == ["incident 0", "incident 1"]
    assert _titles(second) == ["incident 2", "incident 3"]
    assert _titles(third) == ["incident 4"]
    assert [(r.total, r.page, r.pages) for r in (first, second, third)] == [
        (5, 1, 3),
        (5, 2, 3),
        (5, 3, 3),
    ]
    assert (first.prev_url, first.next_url) == ("", f"{base}?page=2")
    assert (second.prev_url, second.next_url) == (base, f"{base}?page=3")
    assert (third.prev_url, third.next_url) == (f"{base}?page=2", "")
    assert [r.url for r in (first, second, third)] == [base, f"{base}?page=2", f"{base}?page=3"]


@pytest.mark.django_db
def test_a_full_last_page_has_no_next_link(monkeypatch):
    monkeypatch.setattr(incidents, "PAGE_SIZE", 2)
    for index in range(4):
        _incident(f"incident {index}")

    result = build_incident_list(IncidentFilter(page=2))

    assert (result.pages, result.next_url, len(result.rows)) == (2, "", 2)


@pytest.mark.django_db
def test_a_page_past_the_end_clamps_to_the_last_page(monkeypatch, base):
    monkeypatch.setattr(incidents, "PAGE_SIZE", 2)
    for index in range(3):
        _incident(f"incident {index}", age_hours=index)

    result = build_incident_list(IncidentFilter(page=99))

    assert _titles(result) == ["incident 2"]
    assert (result.page, result.pages) == (2, 2)
    assert result.url == f"{base}?page=2"
    assert (result.prev_url, result.next_url) == (base, "")
    assert result.notice == ""


@pytest.mark.django_db
def test_pagination_links_keep_the_filters(monkeypatch, base):
    monkeypatch.setattr(incidents, "PAGE_SIZE", 1)
    node = Node.objects.create(instance_id="web-1")
    for index in range(3):
        _alert(_incident(f"incident {index}", "critical", status="resolved"), node)

    result = build_incident_list(
        IncidentFilter(status="resolved", severity="critical", node=node.pk, since="7d", page=2)
    )

    query = f"?status=resolved&severity=critical&node={node.pk}&since=7d"
    assert result.prev_url == f"{base}{query}"
    assert result.next_url == f"{base}{query}&page=3"
    assert result.url == f"{base}{query}&page=2"


@pytest.mark.django_db
def test_chip_groups_carry_labels_keys_and_the_active_choice():
    result = build_incident_list(IncidentFilter(status="resolved", since="7d"))

    assert [(group.name, group.label) for group in result.groups] == [
        ("status", "Status"),
        ("severity", "Severity"),
        ("since", "Since"),
    ]
    assert [(chip.key, chip.label, chip.active) for chip in _group(result, "status").chips] == [
        ("active", "Active", False),
        ("open", "Open", False),
        ("acknowledged", "Acknowledged", False),
        ("resolved", "Resolved", True),
        ("closed", "Closed", False),
        ("all", "All", False),
    ]
    assert [(chip.key, chip.label, chip.active) for chip in _group(result, "severity").chips] == [
        ("any", "Any severity", True),
        ("critical", "Critical", False),
        ("warning", "Warning", False),
        ("info", "Info", False),
    ]
    assert [(chip.key, chip.label, chip.active) for chip in _group(result, "since").chips] == [
        ("any", "Any time", False),
        ("24h", "24h", False),
        ("7d", "7d", True),
        ("30d", "30d", False),
    ]


@pytest.mark.django_db
def test_the_node_group_lists_every_node_by_name(base):
    zeta = Node.objects.create(instance_id="n1", hostname="Zeta")
    alpha = Node.objects.create(instance_id="n2", hostname="alpha")
    bare = Node.objects.create(instance_id="beta-id", hostname="")

    result = build_incident_list(IncidentFilter(node=bare.pk))

    group = _group(result, "node")
    assert [group.name for group in result.groups] == ["status", "severity", "node", "since"]
    assert group.label == "Node"
    assert [(chip.key, chip.label, chip.active, chip.url) for chip in group.chips] == [
        ("any", "Any node", False, base),
        (str(alpha.pk), "alpha", False, f"{base}?node={alpha.pk}"),
        (str(bare.pk), "beta-id", True, f"{base}?node={bare.pk}"),
        (str(zeta.pk), "Zeta", False, f"{base}?node={zeta.pk}"),
    ]


@pytest.mark.django_db
def test_a_chip_changes_its_own_field_keeps_the_others_and_resets_the_page(monkeypatch, base):
    monkeypatch.setattr(incidents, "PAGE_SIZE", 1)
    for index in range(3):
        _incident(f"incident {index}", "critical")

    result = build_incident_list(IncidentFilter(status="all", severity="critical", page=2))

    assert result.page == 2
    status = {chip.key: chip.url for chip in _group(result, "status").chips}
    severity = {chip.key: chip.url for chip in _group(result, "severity").chips}
    since = {chip.key: chip.url for chip in _group(result, "since").chips}
    assert status["active"] == f"{base}?severity=critical"
    assert status["closed"] == f"{base}?status=closed&severity=critical"
    assert status["all"] == f"{base}?status=all&severity=critical"
    assert severity["any"] == f"{base}?status=all"
    assert severity["info"] == f"{base}?status=all&severity=info"
    assert since["any"] == f"{base}?status=all&severity=critical"
    assert since["24h"] == f"{base}?status=all&severity=critical&since=24h"


@pytest.mark.django_db
def test_the_result_is_frozen_and_holds_tuples():
    Node.objects.create(instance_id="web-1")
    _incident("one")

    result = build_incident_list(IncidentFilter())

    assert isinstance(result.rows, tuple)
    assert isinstance(result.groups, tuple)
    assert all(isinstance(group.chips, tuple) for group in result.groups)
    with pytest.raises(FrozenInstanceError):
        result.total = 9
    with pytest.raises(FrozenInstanceError):
        result.rows[0].title = "x"
    with pytest.raises(FrozenInstanceError):
        result.groups[0].chips[0].active = True
    with pytest.raises(FrozenInstanceError):
        result.groups[0].name = "x"


@pytest.mark.django_db
@pytest.mark.parametrize("count", [1, 7])
def test_the_query_count_does_not_grow_with_the_page(django_assert_num_queries, count):
    nodes = [Node.objects.create(instance_id=f"web-{index}") for index in range(3)]
    for index in range(count):
        incident = _incident(f"incident {index}")
        for node in nodes:
            _alert(incident, node, key=node.instance_id)

    # Node chips, count, page rows, node names for the page.
    with django_assert_num_queries(4):
        result = build_incident_list(IncidentFilter(node=nodes[0].pk, since="7d"))

    assert len(result.rows) == count
    assert {row.nodes for row in result.rows} == {"web-0 +2"}


@pytest.mark.django_db
def test_an_empty_page_costs_the_any_incident_check_instead_of_the_row_queries(
    django_assert_num_queries,
):
    _incident("resolved", status="resolved")

    # Node chips, count, any-incident check.
    with django_assert_num_queries(3):
        result = build_incident_list(IncidentFilter())

    assert result.empty == "none_active"
