"""Base node handler and types for pipeline nodes."""

import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class NodeType(Enum):
    """Types of nodes in a pipeline."""

    INGEST = "ingest"  # Alert ingestion (creates Incident/Alert)
    CONTEXT = "context"  # Gather system context (CPU, memory, disk)
    INTELLIGENCE = "intelligence"  # AI analysis providers
    NOTIFY = "notify"  # Notification drivers
    TRANSFORM = "transform"  # Data transformation
    CONDITION = "condition"  # Conditional branching


@dataclass
class NodeContext:
    """
    Context passed to node handlers.

    Contains the accumulated state from previous nodes.
    """

    trace_id: str
    run_id: str
    incident_id: int | None = None
    payload: Dict[str, Any] = field(default_factory=dict)
    previous_outputs: Dict[str, Any] = field(default_factory=dict)
    environment: str = "production"
    source: str = "unknown"

    def get_previous(self, node_id: str) -> Dict[str, Any]:
        """Get output from a previous node."""
        return self.previous_outputs.get(node_id, {})


@dataclass
class NodeResult:
    """
    Result from a node handler execution.

    Contains the output data and any errors.
    """

    node_id: str
    node_type: str
    output: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "output": self.output,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@contextmanager
def track_duration(result: "NodeResult"):
    """Automatically set ``result.duration_ms`` on exit (normal or exception)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        result.duration_ms = (time.perf_counter() - start) * 1000


class BaseNodeHandler(ABC):
    """
    Abstract base class for pipeline node handlers.

    Each node type implements this interface to process
    data in the pipeline.
    """

    node_type: NodeType = NodeType.CONTEXT
    name: str = "base"

    @abstractmethod
    def execute(self, ctx: NodeContext, config: Dict[str, Any]) -> NodeResult:
        """
        Execute the node with given context and configuration.

        Args:
            ctx: Node context with previous outputs and metadata.
            config: Node-specific configuration from pipeline definition.

        Returns:
            NodeResult with output data and any errors.
        """
        raise NotImplementedError

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        Validate the node configuration.

        Args:
            config: Node configuration to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        return []
