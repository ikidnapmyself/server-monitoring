"""Notify node handler (simple stub for tests)."""

from typing import Any, Dict

from apps.orchestration.nodes.base import BaseNodeHandler, NodeContext, NodeResult, NodeType


class NotifyNodeHandler(BaseNodeHandler):
    node_type = NodeType.NOTIFY
    name = "notify"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        node_id = config.get("id", "notify")
        result = NodeResult(node_id=node_id, node_type="notify")

        driver = config.get("driver")
        if not driver:
            result.errors.append("Missing driver in notify config")
            return result

        # Simulate sending: echo the message into output
        result.output = {"delivered": True, "driver": driver}
        return result

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        errors = []
        if "driver" not in config:
            errors.append("'driver' is required for notify nodes")
        return errors
