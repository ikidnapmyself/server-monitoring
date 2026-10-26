"""Read model for the confirm page that allows a node's listening ports."""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Alert, Node
from apps.alerts.node_policy import allowlist_with, config_digest
from apps.console.queries.hosts import node_name

CHECKER = "listening_ports"


@dataclass(frozen=True)
class AllowPorts:
    pk: int
    name: str
    ports: tuple[int, ...]
    after: tuple[int, ...]
    seeded: tuple[int, ...]
    digest: str
    config: dict
    back_url: str
    url: str


def parse_ports(raw: str) -> tuple[int, ...]:
    ports = {int(token) for token in raw.split(",") if token.strip().isdigit()}
    return tuple(sorted(port for port in ports if 1 <= port <= 65535))


def _tolerated(node: Node) -> tuple[int, ...]:
    """Ports the node reports as listening that its policy does not flag today."""
    alert = (
        Alert.objects.filter(
            labels__instance_id=node.instance_id, labels__checker=CHECKER, status="firing"
        )
        .order_by("-updated_at")
        .first()
    )
    metrics = (
        alert.raw_payload.get("metrics") if alert and isinstance(alert.raw_payload, dict) else {}
    )
    metrics = metrics if isinstance(metrics, dict) else {}
    flagged = set(metrics.get("unexpected_ports") or [])
    listening = metrics.get("listening") or []
    return tuple(
        sorted(
            {
                row["port"]
                for row in listening
                if isinstance(row, dict) and isinstance(row.get("port"), int)
            }
            - flagged
        )
    )


def build_allow_ports(pk: int, ports: tuple[int, ...]) -> AllowPorts | None:
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    tolerated = _tolerated(node)
    after, config = allowlist_with(node.config, ports, tolerated)
    stored = (node.config or {}).get(CHECKER, {}) if isinstance(node.config, dict) else {}
    had_list = isinstance(stored, dict) and isinstance(stored.get("allowlist"), list)
    return AllowPorts(
        pk=node.pk,
        name=node_name(node.hostname),
        ports=ports,
        after=tuple(after),
        seeded=() if had_list else tuple(port for port in tolerated if port not in ports),
        digest=config_digest(node.config),
        config=config,
        back_url=reverse("console:node", args=[node.pk]),
        url=reverse("console:node-allow-ports", args=[node.pk]),
    )
