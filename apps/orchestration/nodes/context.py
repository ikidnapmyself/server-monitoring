"""Context node handler (gathers context information)."""

from typing import Any, Dict

from apps.orchestration.nodes.base import BaseNodeHandler, NodeContext, NodeResult, NodeType


class ContextNodeHandler(BaseNodeHandler):
    node_type = NodeType.CONTEXT
    name = "context"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        node_id = config.get("id", "context")
        result = NodeResult(node_id=node_id, node_type="context")

        include = config.get("include", ["cpu", "memory"]) or ["cpu", "memory"]
        # Return deterministic fake context for tests
        data = {}
        if "cpu" in include:
            data["cpu"] = {"load": 0.5}
        if "memory" in include:
            data["memory"] = {"used_mb": 512}
        if "disk" in include:
            data["disk"] = {"free_gb": 10}

        result.output = {"context": data}
        return result

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        # No required fields for context nodes
        return []
