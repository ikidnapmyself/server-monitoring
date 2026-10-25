import json

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Node
from apps.console._tests.queries.test_checks import _check
from apps.console.queries.check import build_check_detail, thresholds
from apps.console.queries.checks import describe_check_run
from apps.orchestration.models import PipelineRun
from config.describing import Action, Fact

pytestmark = pytest.mark.django_db


def test_a_missing_check_run_is_none():
    assert build_check_detail(404) is None


def test_the_detail_describes_the_run_and_keeps_raw_values_in_details():
    PipelineRun.objects.create(run_id="r-1", trace_id="trace-1")
    run = _check(
        status="critical",
        error="psutil exploded",
        trace_id="trace-1",
        metrics={"cpu_percent": 99.5},
        duration_ms=250,
        warning_threshold=80,
        critical_threshold=95.5,
    )

    detail = build_check_detail(run.pk)

    described = describe_check_run(run)
    assert detail.what.headline == described.headline
    assert detail.what.facts[-3:] == (
        Fact("Status", "critical"),
        Fact("Error", "psutil exploded"),
        Fact("Trace ID", "trace-1", copy=True),
    )
    assert json.loads(detail.raw) == {"cpu_percent": 99.5}
    assert detail.thresholds == "Warns at 80, critical at 95.5"
    assert detail.took == "250ms"
    assert detail.run == Action("Part of a run", "view_runs", {"q": "trace-1"})
    assert detail.alert is None


def test_a_bare_run_has_no_links_and_only_its_status_in_details():
    run = _check(hostname="")

    detail = build_check_detail(run.pk)

    assert detail.what.facts[-1] == Fact("Status", "ok")
    assert "Error" not in [fact.label for fact in detail.what.facts]
    assert (detail.run, detail.alert, detail.node, detail.thresholds) == (None, None, None, "")


def test_a_trace_without_a_pipeline_run_is_not_part_of_a_run():
    run = _check(trace_id="minted-by-a-quiet-rerun")

    detail = build_check_detail(run.pk)

    assert detail.run is None
    assert Fact("Trace ID", "minted-by-a-quiet-rerun", copy=True) in detail.what.facts


def test_the_raised_alert_links_the_alert():
    alert = Alert.objects.create(
        fingerprint="f",
        name="cpu",
        severity="critical",
        source="cluster",
        started_at=timezone.now(),
    )
    run = _check(alert=alert)

    assert build_check_detail(run.pk).alert == Action(
        f"Alert #{alert.pk}", "view_alert", {"pk": alert.pk}
    )


def test_the_node_links_only_when_one_node_has_the_hostname():
    run = _check(hostname="web")
    assert build_check_detail(run.pk).node is None

    node = Node.objects.create(instance_id="i-1", hostname="web")
    assert build_check_detail(run.pk).node == Action("web", "view_node", {"pk": node.pk})

    Node.objects.create(instance_id="i-2", hostname="web")
    assert build_check_detail(run.pk).node is None


@pytest.mark.parametrize(
    ("warning", "critical", "text"),
    [
        (None, None, ""),
        (80.0, None, "Warns at 80"),
        (None, 90.0, "Critical at 90"),
        (0.0, 0.5, "Warns at 0, critical at 0.5"),
        (12500000.0, 1e20, "Warns at 12500000, critical at 100000000000000000000"),
    ],
)
def test_thresholds_leave_out_an_unset_side(warning, critical, text):
    assert thresholds(warning, critical) == text
