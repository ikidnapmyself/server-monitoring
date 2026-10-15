"""Which machines incidents are about, read off their alerts.

An alert names its machine through a linked ``Node`` when ingest found one, and
otherwise only through its labels. Most checker alerts carry the labels alone, so
reading the ``Node`` link by itself leaves the host blank. This follows the admin's
``Host`` column.
"""

from collections import defaultdict
from collections.abc import Iterable

from apps.alerts.models import Alert
from apps.alerts.services import instance_key_from_labels


def incident_hosts(incident_pks: Iterable[int]) -> dict[int, tuple[str, ...]]:
    found: defaultdict[int, set[str]] = defaultdict(set)
    rows = (
        Alert.objects.filter(incident_id__in=list(incident_pks))
        .values_list("incident_id", "node__hostname", "node__instance_id", "labels")
        .order_by()
    )
    for incident_pk, hostname, instance_id, labels in rows:
        name = hostname or instance_id or instance_key_from_labels(labels)
        if isinstance(name, str) and name:
            found[incident_pk].add(name)
    return {pk: tuple(sorted(names, key=str.casefold)) for pk, names in found.items()}


def host_summary(hosts: tuple[str, ...]) -> str:
    if len(hosts) < 2:
        return "".join(hosts)
    return f"{hosts[0]} +{len(hosts) - 1}"
