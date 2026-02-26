# apps/orchestration/nodes/transform.py
"""Transform node handler for data transformation."""

import logging
import time
from typing import Any

from apps.orchestration.nodes.base import (
    BaseNodeHandler,
    NodeContext,
    NodeResult,
    NodeType,
)

logger = logging.getLogger(__name__)


class TransformNodeHandler(BaseNodeHandler):
    """
    Node handler for data transformation.

    Transforms data from previous nodes using simple
    extraction, filtering, and mapping operations.
    """

    node_type = NodeType.TRANSFORM
    name = "transform"

    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """Execute data transformation."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "transform"),
            node_type="transform",
        )

        try:
            source_node: str = config.get("source_node", "")
            source_data = ctx.previous_outputs.get(source_node, {}) if source_node else {}

            # Extract specific field
            extract_path = config.get("extract")
            if extract_path:
                source_data = self._get_nested(source_data, extract_path)

            # Apply filter
            filter_priority = config.get("filter_priority")
            if filter_priority and isinstance(source_data, list):
                source_data = [
                    item
                    for item in source_data
                    if isinstance(item, dict)
                    and item.get("priority", "").lower() == filter_priority.lower()
                ]

            # Apply mapping
            mapping = config.get("mapping")
            if mapping and source_node:
                mapped = {}
                for target_key, source_path in mapping.items():
                    value = self._get_nested(ctx.previous_outputs.get(source_node, {}), source_path)
                    mapped[target_key] = value
                result.output = {"transformed": mapped, "source_node": source_node}
            else:
                result.output = {"transformed": source_data, "source_node": source_node}

        except Exception as e:
            logger.exception("Error in TransformNodeHandler: %s", e)
            result.errors.append(f"Transform error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def _get_nested(self, data: Any, path: str) -> Any:
        """Get nested value from dict using dot notation."""
        if not path:
            return data
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return current

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate transform node configuration."""
        errors = []

        if "source_node" not in config:
            errors.append("Missing required field: source_node")

        return errors
