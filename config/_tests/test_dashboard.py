"""Tests for config/dashboard.py — prettify_json and get_dashboard_context."""

import pytest
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from config.dashboard import get_dashboard_context, prettify_json


class TestPrettifyJson(SimpleTestCase):
    def test_renders_dict_as_pre_block(self):
        result = prettify_json({"key": "value"})
        assert "<pre" in result
        assert "key" in result

    def test_renders_empty_dict(self):
        result = prettify_json({})
        assert "{}" in result

    def test_none_returns_dash(self):
        assert prettify_json(None) == "-"

    def test_renders_nested_structure(self):
        result = prettify_json({"a": {"b": [1, 2]}})
        assert "<pre" in result


@pytest.mark.django_db
class TestGetDashboardContext(TestCase):
    def test_returns_expected_keys(self):
        ctx = get_dashboard_context()
        expected_keys = {
            "active_incidents",
            "pipeline_health",
            "recent_check_runs",
            "failed_pipelines",
            "top_failing_checkers",
            "top_error_types",
            "provider_usage",
            "readiness",
            "fleet_metrics",
        }
        assert set(ctx.keys()) == expected_keys

    def test_pipeline_health_structure(self):
        ctx = get_dashboard_context()
        health = ctx["pipeline_health"]
        assert "total" in health
        assert "successful" in health
        assert "failed" in health
        assert "success_rate" in health

    def test_empty_database_returns_zeros(self):
        ctx = get_dashboard_context()
        assert ctx["pipeline_health"]["total"] == 0
        assert ctx["pipeline_health"]["success_rate"] == 0
        assert ctx["active_incidents"]["total"] == 0

    def test_with_data(self):
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus
        from apps.orchestration.models import PipelineRun, PipelineStatus

        Incident.objects.create(
            title="Test", severity=AlertSeverity.CRITICAL, status=IncidentStatus.OPEN
        )
        PipelineRun.objects.create(
            trace_id="t1",
            run_id="r1",
            status=PipelineStatus.NOTIFIED,
            created_at=timezone.now(),
        )

        ctx = get_dashboard_context()
        assert ctx["active_incidents"]["total"] == 1
        assert ctx["active_incidents"]["critical"] == 1
        assert ctx["pipeline_health"]["total"] == 1
        assert ctx["pipeline_health"]["success_rate"] == 100.0


@pytest.mark.django_db
class TestFleetMetrics:
    """The headline readings grid on the dashboard.

    build_checker_rows is tested in apps/alerts/_tests, so this covers the
    shaping the dashboard adds: which columns exist, and what a node that does
    not report a column gets in its place.
    """

    def _peer(self, instance_id, checker, metrics, severity="info"):
        from django.utils import timezone

        from apps.alerts.models import Alert, Node

        node = Node.objects.create(instance_id=instance_id, hostname=instance_id)
        Alert.objects.create(
            fingerprint=f"{instance_id}:{checker}",
            name=checker,
            severity=severity,
            status="firing",
            source="cluster",
            node=node,
            labels={"checker": checker, "instance_id": instance_id},
            annotations=metrics,
            started_at=timezone.now(),
        )
        return node

    def test_no_nodes_means_no_columns_and_no_rows(self):
        from config.dashboard import build_fleet_metrics

        metrics = build_fleet_metrics()
        assert metrics.columns == []
        assert metrics.rows == []

    def test_columns_cover_only_what_a_node_reports(self):
        from config.dashboard import build_fleet_metrics

        self._peer("peer-a", "cpu", {"cpu_percent": "41.5"})
        metrics = build_fleet_metrics()
        assert [c.checker for c in metrics.columns] == ["cpu"]
        assert [c.unit for c in metrics.columns] == ["%"]

    def test_a_node_missing_a_column_gets_a_dash_and_no_colour(self):
        from config.dashboard import build_fleet_metrics

        self._peer("peer-a", "cpu", {"cpu_percent": "41.5"})
        self._peer("peer-b", "memory", {"memory_percent": "88.0"}, severity="warning")
        metrics = build_fleet_metrics()
        assert [c.checker for c in metrics.columns] == ["cpu", "memory"]
        rows = {row.instance_id: row for row in metrics.rows}
        assert [(c.value, c.status) for c in rows["peer-a"].cells] == [("41.5", "ok"), ("—", "")]
        assert [(c.value, c.status) for c in rows["peer-b"].cells] == [
            ("—", ""),
            ("88.0", "warning"),
        ]

    def test_a_node_reporting_no_headline_metric_is_not_a_row(self):
        from config.dashboard import build_fleet_metrics

        # network has no PRIMARY_METRIC, so it is not a column and this node has
        # nothing to put in the grid.
        self._peer("peer-a", "network", {"latency_ms": "12"})
        assert build_fleet_metrics().rows == []

    def test_the_row_links_to_the_node(self):
        from django.urls import reverse

        from config.dashboard import build_fleet_metrics

        node = self._peer("peer-a", "cpu", {"cpu_percent": "41.5"})
        (row,) = build_fleet_metrics().rows
        assert row.url == reverse("admin:alerts_node_change", args=[node.pk])


@pytest.mark.django_db
class TestTrendRowsAreReachable:
    """Every trend row names something filterable, so every row is a link.

    A count with nowhere to click is the dead-end these cover: the operator can
    see that ``disk`` failed twelve times and has to rebuild the filter by hand.
    """

    def test_a_failing_checker_links_its_own_check_runs(self, admin_client):
        from django.urls import reverse

        from apps.checkers.models import CheckRun, CheckStatus

        CheckRun.objects.create(
            checker_name="disk", hostname="h", status=CheckStatus.CRITICAL, message="m"
        )
        row = get_dashboard_context()["top_failing_checkers"][0]
        expected = reverse("admin:checkers_checkrun_changelist") + "?checker_name__exact=disk"
        assert row["url"] == expected
        assert admin_client.get(expected).status_code == 200

    def test_an_error_type_links_the_failed_runs_that_raised_it(self, admin_client):
        from django.urls import reverse

        from apps.orchestration.models import PipelineRun, PipelineStatus

        PipelineRun.objects.create(
            trace_id="t",
            run_id="r",
            status=PipelineStatus.FAILED,
            last_error_type="TimeoutError",
        )
        row = get_dashboard_context()["top_error_types"][0]
        expected = (
            reverse("admin:orchestration_pipelinerun_changelist")
            + "?last_error_type__exact=TimeoutError&status__exact=failed"
        )
        assert row["url"] == expected
        assert admin_client.get(expected).status_code == 200

    def test_a_provider_links_its_analysis_runs(self, admin_client):
        from django.urls import reverse

        from apps.intelligence.models import AnalysisRun

        AnalysisRun.objects.create(trace_id="t", provider="anthropic", status="succeeded")
        row = get_dashboard_context()["provider_usage"][0]
        expected = (
            reverse("admin:intelligence_analysisrun_changelist") + "?provider__exact=anthropic"
        )
        assert row["url"] == expected
        assert admin_client.get(expected).status_code == 200

    def test_a_recent_check_run_links_its_own_row(self, admin_client):
        from django.urls import reverse

        from apps.checkers.models import CheckRun, CheckStatus

        run = CheckRun.objects.create(
            checker_name="cpu", hostname="h", status=CheckStatus.OK, message="m"
        )
        body = admin_client.get(reverse("admin:index")).content.decode()
        url = reverse("admin:checkers_checkrun_change", args=[run.pk])
        assert f'<a href="{url}">cpu</a>' in body
