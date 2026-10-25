from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.db.models import Q
from django.http import QueryDict

from apps.alerts.models import AlertSeverity, IncidentStatus
from apps.checkers.models import CheckRun, CheckStatus
from apps.console.filters import (
    RUN_STATUS_CHOICES,
    AlertFilter,
    AnalysisFilter,
    CheckFilter,
    InboxFilter,
    IncidentFilter,
    NodeFilter,
    PreflightFilter,
    RunFilter,
    checker_param,
)
from apps.intelligence.models import AnalysisRun, AnalysisRunStatus
from apps.orchestration.models import PipelineRun, PipelineStatus

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


def _alert_filter(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return AlertFilter.parse(query)


def test_alert_filter_defaults_to_firing():
    assert _alert_filter() == AlertFilter(status="firing")
    assert AlertFilter().statuses() == ("firing",)
    assert AlertFilter(status="all").statuses() == ()


def test_alert_filter_keeps_valid_values_and_drops_the_rest():
    criteria = _alert_filter(
        status="resolved", severity="bogus", node="3", source="grafana", since="7d", page="x"
    )
    assert criteria == AlertFilter(
        status="resolved", node=3, source="grafana", since="7d", dropped=("severity", "page")
    )


def test_alert_filter_search_is_trimmed_capped_and_cleaned():
    criteria = _alert_filter(q="  cpu\x00 " + "x" * 300)
    assert criteria.q.startswith("cpu ")
    assert len(criteria.q) == 255
    assert "\x00" not in criteria.q


def test_alert_filter_querystring_resets_the_page_and_skips_defaults():
    criteria = AlertFilter(q="cpu", page=3)
    assert criteria.querystring(page=4) == "?q=cpu&page=4"
    assert criteria.querystring(status="all") == "?q=cpu&status=all"
    assert AlertFilter().querystring() == ""


def test_alert_filter_without_drops_named_fields():
    criteria = AlertFilter(node=9, source="x").without("node", "source")
    assert criteria == AlertFilter(dropped=("node", "source"))


def test_alert_filter_kept_lists_what_the_search_form_must_carry():
    criteria = AlertFilter(q="cpu", status="all", node=2, page=5)
    assert criteria.kept() == (("status", "all"), ("node", "2"))


def _run_filter(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return RunFilter.parse(query)


def test_run_filter_defaults_to_every_status():
    assert _run_filter() == RunFilter()
    assert RunFilter().matches() == Q()
    assert RunFilter(status="active").matches() == RUN_STATUS_CHOICES["active"]


def test_run_filter_keeps_valid_values_and_drops_the_rest():
    criteria = _run_filter(status="failed", origin="bogus", node="3", since="24h", page="0")
    assert criteria == RunFilter(status="failed", node=3, since="24h", dropped=("origin", "page"))
    assert _run_filter(origin="checker_generated").origin == "checker_generated"


def test_run_filter_search_is_trimmed_capped_and_cleaned():
    criteria = _run_filter(q="  abc\x00 " + "x" * 300)
    assert criteria.q.startswith("abc ")
    assert len(criteria.q) == 255
    assert "\x00" not in criteria.q
    assert _run_filter(q="   ").q == ""


def test_run_filter_cutoff_counts_back_from_now():
    assert RunFilter().cutoff(NOW) is None
    assert RunFilter(since="7d").cutoff(NOW) == NOW - timedelta(days=7)


def test_run_filter_querystring_resets_the_page_and_skips_defaults():
    criteria = RunFilter(status="failed", page=3)
    assert criteria.querystring(origin="manual") == "?status=failed&origin=manual"
    assert criteria.querystring(page=2) == "?status=failed&page=2"
    assert RunFilter().querystring() == ""


def test_run_filter_without_drops_named_fields():
    criteria = RunFilter(node=9, origin="manual", dropped=("since",)).without("node")
    assert criteria == RunFilter(origin="manual", dropped=("node", "since"))


def test_run_filter_kept_lists_what_the_search_form_must_carry():
    criteria = RunFilter(q="abc", status="failed", origin="manual", node=2, since="24h", page=5)
    assert criteria.kept() == (
        ("status", "failed"),
        ("origin", "manual"),
        ("node", "2"),
        ("since", "24h"),
    )


def test_inbox_filter_takes_pending_processing_stuck_or_all():
    for status in ("", "pending", "processing", "stuck"):
        assert InboxFilter.parse(QueryDict(f"status={status}")).status == status
    assert InboxFilter.parse(QueryDict("status=failed")) == InboxFilter(dropped=("status",))
    assert InboxFilter(status="pending").querystring() == "?status=pending"
    assert InboxFilter(status="pending").querystring(status="") == ""
    assert InboxFilter().querystring() == ""


def test_node_filter_parses_freshness_and_a_trimmed_search():
    assert NodeFilter.parse(QueryDict("")) == NodeFilter()
    assert NodeFilter.parse(QueryDict("freshness=silent&q=+web+")) == NodeFilter(
        q="web", freshness="silent"
    )
    assert NodeFilter.parse(QueryDict("freshness=gone")) == NodeFilter(dropped=("freshness",))


def test_node_filter_querystring_and_kept():
    assert NodeFilter(q="web", freshness="silent").querystring(freshness="") == "?q=web"
    assert NodeFilter().querystring() == ""
    assert NodeFilter(freshness="silent").kept() == (("freshness", "silent"),)
    assert NodeFilter(q="web").kept() == ()


def test_checker_param_is_the_trimmed_checker_or_none_for_the_whole_node():
    assert checker_param(QueryDict("checker=+cpu+")) == "cpu"
    assert checker_param(QueryDict("")) is None
    assert checker_param(QueryDict("checker=")) is None
    assert checker_param(QueryDict("checker=+")) == ""
    assert checker_param(QueryDict("checker=%00")) == ""


def _params(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return query


def test_run_filter_takes_a_reason_code_or_other():
    assert RunFilter().reason == ""
    for code in ("no_route", "no_channel", "no_driver", "other"):
        assert RunFilter.parse(_params(reason=code)) == RunFilter(reason=code)
    assert RunFilter.parse(_params(reason="")) == RunFilter()
    assert RunFilter.parse(_params(reason="StageExecutionError")) == RunFilter(dropped=("reason",))


def test_run_filter_reason_round_trips_and_is_kept():
    criteria = RunFilter(status="failed", reason="no_route", since="7d")
    assert criteria.querystring() == "?status=failed&reason=no_route&since=7d"
    assert RunFilter.parse(QueryDict(criteria.querystring().lstrip("?"))) == criteria
    assert criteria.kept() == (("status", "failed"), ("reason", "no_route"), ("since", "7d"))


@pytest.mark.django_db
def test_run_filter_reason_selects_the_runs_it_names():
    def run(name, message, status=PipelineStatus.FAILED):
        return PipelineRun.objects.create(
            run_id=name, trace_id=name, status=status, last_error_message=message
        )

    routed = run("routed", "no_route: nothing matched")
    staged = run("staged", "Stage notify failed: no_channel: route has none")
    driverless = run("driverless", "Stage notify failed: no_driver: gone")
    crashed = run("crashed", "StageExecutionError: boom")
    blank = run("blank", "")
    buried = run("buried", "boom then no_route: later")
    still_going = run("going", "no_route: x", status=PipelineStatus.RETRYING)

    def picked(reason):
        found = PipelineRun.objects.filter(RunFilter(reason=reason).matches())
        return set(found.values_list("run_id", flat=True))

    assert picked("no_route") == {routed.run_id, still_going.run_id}
    assert picked("no_channel") == {staged.run_id}
    assert picked("no_driver") == {driverless.run_id}
    assert picked("other") == {crashed.run_id, blank.run_id, buried.run_id}
    assert len(picked("")) == 7
    failed_routes = PipelineRun.objects.filter(
        RunFilter(status="failed", reason="no_route").matches()
    )
    assert list(failed_routes) == [routed]


def test_check_filter_defaults_and_keeps_valid_values():
    assert CheckFilter.parse(_params()) == CheckFilter()
    assert CheckFilter.parse(_params(status="all")) == CheckFilter()
    assert CheckFilter().statuses() == ()
    assert CheckFilter(status="failing").statuses() == ("warning", "critical", "unknown")
    for status in CheckStatus.values:
        criteria = CheckFilter.parse(_params(status=status))
        assert criteria.status == status
        assert criteria.statuses() == (status,)
    criteria = CheckFilter.parse(_params(status="failing", checker="cpu", since="24h", page="2"))
    assert criteria == CheckFilter(status="failing", checker="cpu", since="24h", page=2)


@pytest.mark.django_db
def test_check_filter_takes_a_checker_the_hub_has_or_one_already_stored():
    CheckRun.objects.create(checker_name="gone_checker", status="ok", message="m")

    assert CheckFilter.parse(_params(checker="gone_checker")).checker == "gone_checker"
    assert CheckFilter.parse(_params(checker="")) == CheckFilter()
    for bad in ("never_heard", "gone_checker\x00", "x" * 101, "CPU"):
        assert CheckFilter.parse(_params(checker=bad)) == CheckFilter(dropped=("checker",))


@pytest.mark.django_db
def test_check_filter_drops_bad_values_in_field_order():
    criteria = CheckFilter.parse(_params(page="0", since="1y", checker="nope", status="bad"))
    assert criteria == CheckFilter(dropped=("status", "checker", "since", "page"))


def test_check_filter_search_and_host_are_trimmed_capped_and_cleaned():
    criteria = CheckFilter.parse(_params(q="  disk\x00 " + "x" * 300, host=" web-1 " + "y" * 300))
    assert criteria.q.startswith("disk ")
    assert len(criteria.q) == 255
    assert criteria.host.startswith("web-1")
    assert len(criteria.host) == 255
    assert CheckFilter.parse(_params(host="   ")).host == ""


def test_check_filter_querystring_round_trips_and_resets_the_page():
    criteria = CheckFilter(
        q="disk", status="failing", checker="cpu", host="web-1", since="7d", page=3
    )
    assert criteria.querystring() == (
        "?q=disk&status=failing&checker=cpu&host=web-1&since=7d&page=3"
    )
    assert criteria.querystring(status="ok") == "?q=disk&status=ok&checker=cpu&host=web-1&since=7d"
    assert CheckFilter().querystring() == ""
    assert CheckFilter.parse(QueryDict(criteria.querystring().lstrip("?"))) == criteria


def test_check_filter_cutoff_and_kept():
    assert CheckFilter().cutoff(NOW) is None
    assert CheckFilter(since="30d").cutoff(NOW) == NOW - timedelta(days=30)
    criteria = CheckFilter(q="x", status="ok", checker="cpu", host="h", since="24h", page=2)
    assert criteria.kept() == (("status", "ok"), ("since", "24h"))


def test_preflight_filter_parses_status_since_and_page():
    assert PreflightFilter.parse(_params()) == PreflightFilter()
    for status in ("", "ok", "warn", "error"):
        assert PreflightFilter.parse(_params(status=status)).status == status
    criteria = PreflightFilter.parse(_params(status="fail", since="7d", page="x"))
    assert criteria == PreflightFilter(since="7d", dropped=("status", "page"))
    assert PreflightFilter(since="24h").cutoff(NOW) == NOW - timedelta(hours=24)
    assert PreflightFilter().cutoff(NOW) is None


def test_preflight_filter_querystring_round_trips():
    criteria = PreflightFilter(status="error", since="30d", page=2)
    assert criteria.querystring() == "?status=error&since=30d&page=2"
    assert criteria.querystring(status="warn") == "?status=warn&since=30d"
    assert PreflightFilter().querystring() == ""
    assert PreflightFilter.parse(QueryDict(criteria.querystring().lstrip("?"))) == criteria


@pytest.mark.django_db
def test_analysis_filter_parses_status_provider_since_and_page():
    AnalysisRun.objects.create(trace_id="t", pipeline_run_id="r", provider="retired_one")

    assert AnalysisFilter.parse(_params()) == AnalysisFilter()
    for status in AnalysisRunStatus.values:
        assert AnalysisFilter.parse(_params(status=status)).status == status
    for provider in ("", "openai", "claude", "local", "retired_one"):
        assert AnalysisFilter.parse(_params(provider=provider)).provider == provider
    criteria = AnalysisFilter.parse(
        _params(status="done", provider="nobody", since="24h", page="-1")
    )
    assert criteria == AnalysisFilter(since="24h", dropped=("status", "provider", "page"))
    assert AnalysisFilter.parse(_params(provider="x\x00")).dropped == ("provider",)


def test_analysis_filter_querystring_round_trips_and_cutoff():
    criteria = AnalysisFilter(status="failed", provider="openai", since="7d", page=4)
    assert criteria.querystring() == "?status=failed&provider=openai&since=7d&page=4"
    assert criteria.querystring(page=5) == "?status=failed&provider=openai&since=7d&page=5"
    assert AnalysisFilter().querystring() == ""
    assert AnalysisFilter(since="7d").cutoff(NOW) == NOW - timedelta(days=7)
    assert AnalysisFilter().cutoff(NOW) is None
