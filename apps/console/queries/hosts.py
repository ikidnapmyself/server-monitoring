"""Which machines incidents are about, read off their alerts.

An alert names its machine through a linked ``Node`` when ingest found one, and
otherwise only through its labels. Most checker alerts carry the labels alone, so
reading the ``Node`` link by itself leaves the host blank. This follows the admin's
``Host`` column.
"""

from collections import defaultdict
from collections.abc import Iterable

from django.db.models import Q

from apps.alerts.describing import node_name
from apps.alerts.models import Alert, Node
from apps.alerts.services import instance_key_from_labels


def alert_host(hostname: str | None, instance_id: str | None, labels: dict | None) -> str:
    """The linked node's name when there is one, else the instance its labels name."""
    if instance_id:
        return node_name(hostname or "")
    name = hostname or instance_key_from_labels(labels)
    return name if isinstance(name, str) else ""


def node_keys(node: Node) -> tuple[str, ...]:
    """The label values that name one node, as ``on_node`` matches them."""
    return tuple(key for key in (node.instance_id, node.hostname) if key)


def node_choices() -> tuple[tuple[int, str, tuple[str, ...]], ...]:
    """Every node as its pk, display name and the label values that name it."""
    nodes = [
        (pk, node_name(hostname), tuple(key for key in (instance_id, hostname) if key))
        for pk, hostname, instance_id in Node.objects.order_by().values_list(
            "pk", "hostname", "instance_id"
        )
    ]
    nodes.sort(key=lambda node: node[1].casefold())
    return tuple(nodes)


def on_node(node_pk: int, node_keys: tuple[str, ...]) -> Q:
    """Alerts about one node.

    Most checker alerts name their machine only in labels, so an unlinked alert also matches
    when its instance label is one of the node's own names.
    """
    named = (
        Q(labels__instance_id__in=node_keys)
        | Q(labels__instance__in=node_keys)
        | Q(labels__hostname__in=node_keys)
    )
    return Q(node_id=node_pk) | Q(node__isnull=True) & named


def incident_hosts(incident_pks: Iterable[int]) -> dict[int, tuple[str, ...]]:
    found: defaultdict[int, set[str]] = defaultdict(set)
    rows = (
        Alert.objects.filter(incident_id__in=list(incident_pks))
        .values_list("incident_id", "node__hostname", "node__instance_id", "labels")
        .order_by()
    )
    for incident_pk, hostname, instance_id, labels in rows:
        name = alert_host(hostname, instance_id, labels)
        if name:
            found[incident_pk].add(name)
    return {pk: tuple(sorted(names, key=str.casefold)) for pk, names in found.items()}


def host_summary(hosts: tuple[str, ...]) -> str:
    if len(hosts) < 2:
        return "".join(hosts)
    return f"{hosts[0]} +{len(hosts) - 1}"
