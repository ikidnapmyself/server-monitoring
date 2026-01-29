"""Intelligence node handler for AI analysis (simple/local implementation)."""

import time
from typing import Any, Dict

from apps.orchestration.nodes.base import BaseNodeHandler, NodeContext, NodeResult, NodeType


class IntelligenceNodeHandler(BaseNodeHandler):
    node_type = NodeType.INTELLIGENCE
    name = "intelligence"

    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        start = time.perf_counter()
        node_id = config.get("id", "intelligence")
        result = NodeResult(node_id=node_id, node_type="intelligence")

        provider = config.get("provider")
        if not provider:
            result.errors.append("Missing provider in config")
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result

        # Simple deterministic output for local provider
        if provider == "local":
            result.output = {"recommendations": ["restart-service", "increase-memory"]}
        else:
            # For unknown providers return a placeholder
            result.output = {"recommendations": [f"provider:{provider}:ok"]}

        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    def validate_config(self, config: Dict[str, Any]) -> list[str]:
        errors = []
        if "provider" not in config:
            errors.append("'provider' is required for intelligence nodes")
        return errors
