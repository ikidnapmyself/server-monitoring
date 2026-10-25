from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.checkers.models import CheckRun, PreflightRun
from apps.console.queries.reports import (
    Fleet,
    FleetCell,
    PipelineHealth,
    ReportRow,
    build_reports,
    failing_checkers,
    failure_reasons,
    fleet,
    latest_preflight,
    pipeline_health,
    provider_usage,
)
from apps.intelligence.models import AnalysisRun
from apps.orchestration.models import PipelineRun
from config.describing import Action, Description

pytestmark = pytest.mark.django_db


def _run(status, message="", hours=1):
    run = PipelineRun.objects.create(
        run_id=f"r-{PipelineRun.objects.count()}",
        trace_id="t",
        status=status,
        last_error_message=message,
    )
    PipelineRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=hours))
    return run


def _check(checker, status, days=1):
    run = CheckRun.objects.create(checker_name=checker, status=status, message="m")
    CheckRun.objects.filter(pk=run.pk).update(executed_at=timezone.now() - timedelta(days=days))


def _analysis(provider, tokens=None, days=1):
    run = AnalysisRun.objects.create(
        trace_id="t", pipeline_run_id="r", provider=provider, total_tokens=tokens
    )
    AnalysisRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=days))


def test_pipeline_health_counts_the_runs_chips_over_24_hours():
    _run("notified")
    _run("notified")
    _run("checked")
    _run("failed")
    _run("retrying")
    _run("failed", hours=30)

    health = pipeline_health(timezone.now())

    assert health.headline == "40% of runs finished"
    assert health.rows == (
        ReportRow(
            "Finished", "2", Action("Finished", "view_runs", {"status": "finished", "since": "24h"})
        ),
        ReportRow(
            "Failed", "1", Action("Failed", "view_runs", {"status": "failed", "since": "24h"})
        ),
        ReportRow(
            "Still going",
            "2",
            Action("Still going", "view_runs", {"status": "active", "since": "24h"}),
        ),
    )


def test_pipeline_health_with_no_runs_says_so():
    _run("notified", hours=48)

    assert pipeline_health(timezone.now()) == PipelineHealth("No runs in the last 24 hours.", ())


def test_failure_reasons_group_by_reason_code_with_the_runs_chip_wording():
    for _ in range(3):
        _run("failed", "Stage notify failed: no_route: nothing matched")
    _run("failed", "no_channel: route has none")
    _run("failed", "StageExecutionError: boom")
    _run("failed", "")
    _run("failed", "no_route: old", hours=24 * 8)
    _run("retrying", "no_route: not failed")

    rows = failure_reasons(timezone.now())

    assert rows == (
        ReportRow(
            "No route matches this incident",
            "3 failed runs",
            Action(
                "No route matches this incident",
                "view_runs",
                {"status": "failed", "reason": "no_route", "since": "7d"},
            ),
        ),
        ReportRow(
            "Other failures",
            "2 failed runs",
            Action(
                "Other failures",
                "view_runs",
                {"status": "failed", "reason": "other", "since": "7d"},
            ),
        ),
        ReportRow(
            "The matched route has no active channel",
            "1 failed run",
            Action(
                "The matched route has no active channel",
                "view_runs",
                {"status": "failed", "reason": "no_channel", "since": "7d"},
            ),
        ),
    )


def test_failing_checkers_count_warning_critical_and_unknown_over_7_days():
    for status in ("critical", "warning", "unknown"):
        _check("disk", status)
    _check("cpu", "critical")
    _check("cpu", "ok")
    _check("gone_checker", "critical")
    _check("memory", "critical", days=8)

    rows = failing_checkers(timezone.now())

    assert [(row.label, row.count) for row in rows] == [
        ("Disk space", "3 failing results"),
        ("CPU usage", "1 failing result"),
        ("Gone checker", "1 failing result"),
    ]
    assert rows[0].link == Action(
        "Disk space", "view_checks", {"checker": "disk", "status": "failing", "since": "7d"}
    )


def test_failing_checkers_keep_the_top_five():
    for name in ("cpu", "memory", "disk", "network", "process", "raid"):
        _check(name, "critical")

    assert len(failing_checkers(timezone.now())) == 5


def test_provider_usage_labels_each_provider():
    _analysis("openai", 1000)
    _analysis("openai", 500)
    _analysis("local")
    _analysis("retired_one", 1)
    _analysis("claude", 9, days=8)

    rows = provider_usage(timezone.now())

    assert [(row.label, row.count) for row in rows] == [
        ("OpenAI", "2 analyses, 1,500 tokens"),
        ("Built-in local analyzer", "1 analysis, 0 tokens"),
        ("A provider this hub doesn't have", "1 analysis, 1 token"),
    ]
    assert rows[0].link == Action("OpenAI", "view_analyses", {"provider": "openai", "since": "7d"})


def test_latest_preflight_links_the_newest_run():
    assert latest_preflight() == Description("Preflight hasn't run on this hub yet.", "muted")
    PreflightRun.objects.create(overall_status="ok")
    newest = PreflightRun.objects.create(overall_status="error", errors=2)

    assert latest_preflight() == Description(
        "Preflight found 2 errors",
        "critical",
        action=Action("Open the preflight run", "view_preflight_run", {"pk": newest.pk}),
    )


def test_fleet_heads_columns_with_labels_and_names_nodes():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    bare = Node.objects.create(instance_id="i-db", hostname="")
    for target, checker, metrics in (
        (node, "cpu", {"cpu_percent": 91.0}),
        (node, "memory", {"memory_percent": 40.0}),
        (bare, "cpu", {"cpu_percent": 10.0}),
    ):
        Alert.objects.create(
            fingerprint=f"{target.instance_id}:{checker}",
            name=checker,
            severity="critical" if checker == "cpu" and target == node else "info",
            status="firing",
            source="cluster",
            node=target,
            labels={"checker": checker, "instance_id": target.instance_id},
            annotations=metrics,
            started_at=timezone.now(),
        )

    grid = fleet()

    assert grid.columns == ("CPU usage (%)", "Memory usage (%)")
    by_pk = {row.pk: row for row in grid.rows}
    assert by_pk[node.pk].link == Action("web", "view_node", {"pk": node.pk})
    assert by_pk[node.pk].cells[0].tone == "critical"
    assert by_pk[bare.pk].link.text == "Unnamed node"
    assert by_pk[bare.pk].cells[1] == FleetCell("", "")


def test_fleet_words_a_reading_it_cant_format():
    node = Node.objects.create(instance_id="i-web", hostname="web")
    Alert.objects.create(
        fingerprint="i-web:cpu",
        name="cpu",
        severity="warning",
        status="firing",
        source="cluster",
        node=node,
        labels={"checker": "cpu", "instance_id": "i-web"},
        annotations={"cpu_percent": ""},
        started_at=timezone.now(),
    )

    (row,) = fleet().rows

    assert row.cells == (FleetCell("Can't read", "warning"),)


def _user(*codenames):
    user = get_user_model().objects.create_user("reader", is_staff=True)
    for codename in codenames:
        app_label, code = codename.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app_label, codename=code)
        )
    return get_user_model().objects.get(pk=user.pk)


def test_build_reports_leaves_out_what_the_reader_cant_read():
    now = timezone.now()

    nothing = build_reports(_user(), now)
    assert nothing == type(nothing)(None, None, None, None, None, None)

    PipelineRun.objects.all().delete()
    everything = build_reports(get_user_model().objects.create_superuser("root"), now)
    assert everything.health == PipelineHealth("No runs in the last 24 hours.", ())
    assert everything.reasons == everything.checkers == everything.providers == ()
    assert everything.preflight.tone == "muted"
    assert everything.fleet == Fleet((), ())


@pytest.mark.parametrize(
    ("codename", "card"),
    [
        ("orchestration.view_pipelinerun", "health"),
        ("checkers.view_checkrun", "checkers"),
        ("intelligence.view_analysisrun", "providers"),
        ("checkers.view_preflightrun", "preflight"),
        ("alerts.view_node", "fleet"),
    ],
)
def test_one_permission_shows_its_own_card(codename, card):
    reports = build_reports(_user(codename), timezone.now())

    shown = {name for name in reports.__dataclass_fields__ if getattr(reports, name) is not None}
    expected = {"health", "reasons"} if card == "health" else {card}
    assert shown == expected
