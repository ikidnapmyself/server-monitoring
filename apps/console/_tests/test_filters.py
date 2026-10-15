from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.http import QueryDict

from apps.alerts.models import AlertSeverity, IncidentStatus
from apps.console.filters import IncidentFilter

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=dt_timezone.utc)


def _parse(query: str) -> IncidentFilter:
    return IncidentFilter.parse(QueryDict(query))


def test_an_empty_querystring_gives_the_defaults():
    parsed = _parse("")

    assert parsed == IncidentFilter()
    assert (parsed.status, parsed.severity, parsed.node, parsed.since, parsed.page) == (
        "active",
        "",
        None,
        "",
        1,
    )
    assert parsed.dropped == ()


def test_the_filter_is_frozen():
    with pytest.raises(FrozenInstanceError):
        IncidentFilter().status = "all"


@pytest.mark.parametrize(
    ("query", "field", "expected"),
    [
        ("status=active", "status", "active"),
        ("status=open", "status", "open"),
        ("status=acknowledged", "status", "acknowledged"),
        ("status=resolved", "status", "resolved"),
        ("status=closed", "status", "closed"),
        ("status=all", "status", "all"),
        ("severity=", "severity", ""),
        ("severity=critical", "severity", "critical"),
        ("severity=warning", "severity", "warning"),
        ("severity=info", "severity", "info"),
        ("node=1", "node", 1),
        ("node=42", "node", 42),
        ("since=", "since", ""),
        ("since=24h", "since", "24h"),
        ("since=7d", "since", "7d"),
        ("since=30d", "since", "30d"),
        ("page=1", "page", 1),
        ("page=7", "page", 7),
    ],
)
def test_a_valid_value_is_kept(query, field, expected):
    parsed = _parse(query)

    assert getattr(parsed, field) == expected
    assert parsed.dropped == ()


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("status=bogus", "status"),
        ("status=", "status"),
        ("status=OPEN", "status"),
        ("severity=fatal", "severity"),
        ("severity=Critical", "severity"),
        ("node=abc", "node"),
        ("node=", "node"),
        ("node=0", "node"),
        ("node=-3", "node"),
        ("node=1.5", "node"),
        ("node=%2B1", "node"),
        ("node=+1", "node"),
        ("node=%D9%A3", "node"),
        ("node=" + "9" * 19, "node"),
        ("since=1y", "since"),
        ("since=24H", "since"),
        ("page=0", "page"),
        ("page=-1", "page"),
        ("page=two", "page"),
        ("page=", "page"),
        ("page=" + "9" * 5000, "page"),
    ],
)
def test_an_invalid_value_falls_back_to_the_default_and_is_reported(query, field):
    parsed = _parse(query)

    assert getattr(parsed, field) == getattr(IncidentFilter(), field)
    assert parsed.dropped == (field,)


def test_dropped_names_come_in_field_order_whatever_the_query_order():
    parsed = _parse("page=x&since=x&node=x&severity=x&status=x")

    assert parsed.dropped == ("status", "severity", "node", "since", "page")
    assert parsed == IncidentFilter(dropped=parsed.dropped)


def test_one_bad_value_does_not_cost_the_good_ones():
    parsed = _parse("status=resolved&severity=nope&node=4&page=3")

    assert parsed == IncidentFilter(status="resolved", node=4, page=3, dropped=("severity",))


def test_unknown_params_are_ignored_and_not_reported():
    parsed = _parse("q=disk&dropped=status&utm_source=x")

    assert parsed == IncidentFilter()


def test_a_repeated_param_takes_the_last_value():
    assert _parse("status=open&status=closed").status == "closed"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", (IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED)),
        ("open", (IncidentStatus.OPEN,)),
        ("acknowledged", (IncidentStatus.ACKNOWLEDGED,)),
        ("resolved", (IncidentStatus.RESOLVED,)),
        ("closed", (IncidentStatus.CLOSED,)),
        ("all", ()),
    ],
)
def test_statuses_names_the_incident_statuses_selected(status, expected):
    assert IncidentFilter(status=status).statuses() == expected


def test_every_incident_status_and_severity_is_selectable():
    for status in IncidentStatus.values:
        assert _parse(f"status={status}").statuses() == (status,)
    for severity in AlertSeverity.values:
        assert _parse(f"severity={severity}").severity == severity


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        ("", None),
        ("24h", NOW - timedelta(hours=24)),
        ("7d", NOW - timedelta(days=7)),
        ("30d", NOW - timedelta(days=30)),
    ],
)
def test_cutoff_counts_back_from_now(since, expected):
    assert IncidentFilter(since=since).cutoff(NOW) == expected


def test_the_default_filter_has_no_querystring():
    assert IncidentFilter().querystring() == ""
    assert IncidentFilter(dropped=("status", "page")).querystring() == ""


def test_querystring_emits_only_non_defaults_in_field_order():
    full = IncidentFilter(status="all", severity="critical", node=3, since="7d", page=2)

    assert full.querystring() == "?status=all&severity=critical&node=3&since=7d&page=2"
    assert IncidentFilter(since="24h").querystring() == "?since=24h"
    assert IncidentFilter(page=4).querystring() == "?page=4"


def test_querystring_never_emits_dropped():
    assert IncidentFilter(severity="info", dropped=("node",)).querystring() == "?severity=info"


@pytest.mark.parametrize(
    "original",
    [
        IncidentFilter(),
        IncidentFilter(status="closed"),
        IncidentFilter(status="all", severity="warning", node=12, since="30d", page=9),
        IncidentFilter(node=1, page=2),
    ],
)
def test_querystring_round_trips_through_parse(original):
    assert _parse(original.querystring().lstrip("?")) == original


def test_changing_a_field_resets_the_page():
    current = IncidentFilter(status="all", severity="critical", page=5)

    assert current.querystring(severity="warning") == "?status=all&severity=warning"
    assert current.querystring(status="active") == "?severity=critical"
    assert current.querystring(node=8) == "?status=all&severity=critical&node=8"
    assert current.querystring(since="24h") == "?status=all&severity=critical&since=24h"
    assert current.querystring(severity="critical") == "?status=all&severity=critical"


def test_changing_only_the_page_keeps_the_other_fields():
    current = IncidentFilter(status="all", node=2, page=5)

    assert current.querystring(page=6) == "?status=all&node=2&page=6"
    assert current.querystring(page=1) == "?status=all&node=2"


def test_querystring_does_not_change_the_filter_itself():
    current = IncidentFilter(page=5)

    current.querystring(status="all")

    assert current == IncidentFilter(page=5)


def test_every_emitted_value_is_url_safe():
    worst = _parse("status=all&severity=critical&node=77&since=30d&page=12").querystring()

    assert worst == "?status=all&severity=critical&node=77&since=30d&page=12"
    assert not set(worst) & set(" \"'<>#%+;")


def test_with_node_dropped_clears_the_node_and_reports_it():
    current = IncidentFilter(status="all", node=9, page=2, dropped=("since",))

    assert current.with_node_dropped() == IncidentFilter(
        status="all", page=2, dropped=("node", "since")
    )
    assert current.node == 9


def test_with_node_dropped_keeps_dropped_in_field_order():
    current = IncidentFilter(node=9, dropped=("status", "severity", "since", "page"))

    assert current.with_node_dropped().dropped == ("status", "severity", "node", "since", "page")
