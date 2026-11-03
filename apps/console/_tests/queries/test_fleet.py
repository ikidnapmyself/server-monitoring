"""The fleet grid's shaping: which columns exist, and what a node missing one gets."""

import pytest

from apps.console.queries.fleet import build_fleet_metrics


@pytest.mark.django_db
class TestFleetMetrics:
    """The headline readings grid on Reports.

    build_checker_rows is tested in apps/alerts/_tests, so this covers the
    shaping the grid adds: which columns exist, and what a node that does
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
        metrics = build_fleet_metrics()
        assert metrics.columns == []
        assert metrics.rows == []

    def test_columns_cover_only_what_a_node_reports(self):
        self._peer("peer-a", "cpu", {"cpu_percent": "41.5"})
        metrics = build_fleet_metrics()
        assert [c.checker for c in metrics.columns] == ["cpu"]
        assert [c.unit for c in metrics.columns] == ["%"]

    def test_a_node_missing_a_column_gets_a_dash_and_no_colour(self):
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
        # network has no PRIMARY_METRIC, so it is not a column and this node has
        # nothing to put in the grid.
        self._peer("peer-a", "network", {"latency_ms": "12"})
        assert build_fleet_metrics().rows == []
