"""Each node's current headline reading per checker, for the Reports fleet grid."""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_overview import build_checker_rows
from apps.alerts.reevaluation import PRIMARY_METRIC

# The unit each headline metric is read in. Keyed by checker rather than by
# metric name because that is what the column is headed with, and the two
# temperature checkers report different metric keys for the same unit.
METRIC_UNITS = {
    "cpu": "%",
    "memory": "%",
    "disk": "%",
    "disk_inodes": "%",
    "io_strain": "%",
    "cpu_temp": "°C",
    "disk_temp": "°C",
}


@dataclass(frozen=True)
class MetricCell:
    """One node's current reading for one checker.

    ``status`` is blank for a checker this node does not report, which is a
    different thing from a reading the hub cannot name. The first gets no
    colour, the second gets "unknown".
    """

    value: str
    status: str


@dataclass(frozen=True)
class NodeMetrics:
    pk: int
    instance_id: str
    hostname: str
    url: str
    cells: list[MetricCell]


@dataclass(frozen=True)
class MetricColumn:
    checker: str
    unit: str


@dataclass(frozen=True)
class FleetMetrics:
    """The numbers the fleet is currently reporting, one row per node.

    ``columns`` is derived from the data, so a checker no node reports is not a
    column of dashes. Empty ``rows`` means no node has reported a headline
    metric yet, which the template says in words.
    """

    columns: list[MetricColumn]
    rows: list[NodeMetrics]


def build_fleet_metrics() -> FleetMetrics:
    """Current headline readings for every node, from whichever source it has.

    Reuses ``node_overview.build_checker_rows``, which is what the Node detail
    page already reads, so a number here and a number there cannot disagree.

    Columns are the checkers with a ``PRIMARY_METRIC``: those are the ones that
    reduce to a single number, which is the only kind a grid cell can hold.

    Cost is one small group of queries per node, the same group the Node page
    pays for one node. The fleet is a handful of machines pushing to one hub,
    so this is bounded by the node table, not by alert or check history.
    """
    headline = sorted(PRIMARY_METRIC)
    readings = {}
    for node in Node.objects.order_by("instance_id"):
        readings[node] = {
            row.checker: row for row in build_checker_rows(node) if row.checker in headline
        }
    checkers = [name for name in headline if any(name in r for r in readings.values())]
    rows = [
        NodeMetrics(
            pk=node.pk,
            instance_id=node.instance_id,
            hostname=node.hostname,
            url=reverse("console:node", args=[node.pk]),
            cells=[
                (
                    MetricCell(value=found.value, status=found.status)
                    if (found := reading.get(name))
                    else MetricCell(value="—", status="")
                )
                for name in checkers
            ],
        )
        for node, reading in readings.items()
        if reading
    ]
    return FleetMetrics(
        columns=[MetricColumn(checker=name, unit=METRIC_UNITS[name]) for name in checkers],
        rows=rows,
    )
