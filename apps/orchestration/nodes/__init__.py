"""
Pipeline node types and handlers.

Nodes are the building blocks of pipelines. Each node type
handles a specific kind of operation (AI analysis, notification, etc.).
"""

from apps.orchestration.nodes.base import (
    BaseNodeHandler,
    NodeContext,
    NodeResult,
    NodeType,
)
from apps.orchestration.nodes.context import ContextNodeHandler
from apps.orchestration.nodes.ingest import IngestNodeHandler
from apps.orchestration.nodes.intelligence import IntelligenceNodeHandler
from apps.orchestration.nodes.notify import NotifyNodeHandler
from apps.orchestration.nodes.transform import TransformNodeHandler

# Registry of node handlers by type
_NODE_HANDLERS: dict[str, type[BaseNodeHandler]] = {}


def register_node_handler(node_type: str, handler_class: type[BaseNodeHandler]) -> None:
    """Register a node handler for a specific type."""
    _NODE_HANDLERS[node_type] = handler_class


def get_node_handler(node_type: str) -> BaseNodeHandler:
    """
    Get a node handler instance by type.

    Args:
        node_type: The node type string (e.g., 'intelligence', 'notify').

    Returns:
        Instantiated node handler.

    Raises:
        KeyError: If node type is not registered.
    """
    if node_type not in _NODE_HANDLERS:
        raise KeyError(f"Unknown node type: {node_type}. Available: {list(_NODE_HANDLERS.keys())}")
    return _NODE_HANDLERS[node_type]()


def list_node_types() -> list[str]:
    """List all registered node types."""
    return list(_NODE_HANDLERS.keys())


# Register built-in handlers
register_node_handler("ingest", IngestNodeHandler)
register_node_handler("intelligence", IntelligenceNodeHandler)
register_node_handler("notify", NotifyNodeHandler)
register_node_handler("context", ContextNodeHandler)
register_node_handler("transform", TransformNodeHandler)


__all__ = [
    "BaseNodeHandler",
    "NodeContext",
    "NodeResult",
    "NodeType",
    "get_node_handler",
    "list_node_types",
    "register_node_handler",
]
