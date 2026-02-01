# Reusable Pipelines Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create configurable, reusable pipelines for orchestration that allow chaining stages dynamically (e.g., alert → OpenAI → notify, or local findings → Claude → notify), managed via Django admin.

**Architecture:** Introduce `PipelineDefinition` model storing JSON-based pipeline configurations. Create node handlers for each stage type (ingest, context, intelligence, notify, transform). Build a `DefinitionBasedOrchestrator` that executes pipelines based on definitions. Add admin interface for pipeline management.

---

## How Pipelines Work

**Two ways to trigger a pipeline:**

```
┌─────────────────────────────────────────────────────────────────┐
│  OPTION A: Alert-Triggered Pipeline                            │
│                                                                 │
│  Grafana/Alertmanager                                          │
│         │                                                       │
│         ▼                                                       │
│  POST /orchestration/pipeline/?definition=my-pipeline           │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────┐    ┌──────────────┐    ┌────────┐                │
│  │  INGEST  │───▶│ INTELLIGENCE │───▶│ NOTIFY │                │
│  │  (alert) │    │   (OpenAI)   │    │(Slack) │                │
│  └──────────┘    └──────────────┘    └────────┘                │
│       │                                                         │
│       ▼                                                         │
│  Creates Incident in DB (id=123)                               │
│  Pipeline uses incident_id for context                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  OPTION B: Standalone Pipeline (scheduled/manual)              │
│                                                                 │
│  Cron job or manual trigger                                    │
│         │                                                       │
│         ▼                                                       │
│  POST /orchestration/definitions/health-check/execute/          │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────┐    ┌──────────────┐    ┌────────┐                │
│  │ CONTEXT  │───▶│ INTELLIGENCE │───▶│ NOTIFY │                │
│  │(cpu/mem) │    │   (local)    │    │(Slack) │                │
│  └──────────┘    └──────────────┘    └────────┘                │
│       │                                                         │
│       ▼                                                         │
│  Gathers system metrics directly                               │
│  No incident created                                           │
└─────────────────────────────────────────────────────────────────┘
```

**Available Node Types:**
- `ingest` - Receive alert webhook, create Incident/Alert in DB
- `context` - Gather system metrics (CPU, memory, disk)
- `intelligence` - AI analysis (local, OpenAI, Claude)
- `transform` - Filter/map data between nodes
- `notify` - Send notifications (Slack, email, PagerDuty)

**Tech Stack:** Django models, JSON schema for pipeline config, existing provider/driver registries, Django admin with custom widgets

---

## Current State Analysis

**Current Pipeline:** Hardcoded 4-stage sequence in `PipelineOrchestrator`:
```
INGEST → CHECK → ANALYZE → NOTIFY
```

**Limitations:**
- Fixed stage order, cannot skip or reorder
- Cannot chain multiple AI providers (OpenAI → Claude)
- Cannot branch based on conditions
- No admin UI for pipeline configuration
- No reusability - must modify code to change flow

**Existing Assets:**
- `apps/intelligence/providers/` - Provider registry with `get_provider(name)`
- `apps/notify/drivers/` - Driver registry with `DRIVER_REGISTRY`
- `apps/orchestration/dtos.py` - Stage context and result DTOs
- `apps/orchestration/executors.py` - BaseExecutor pattern

---

## Implementation Tasks

### Task 1: Create PipelineDefinition model

**Files:**
- Modify: `apps/orchestration/models.py`

**Step 1: Write the failing test**

Create file `apps/orchestration/_tests/test_pipeline_definition.py`:

```python
# apps/orchestration/_tests/test_pipeline_definition.py
"""Tests for PipelineDefinition model."""

import pytest
from django.core.exceptions import ValidationError

from apps.orchestration.models import PipelineDefinition


@pytest.mark.django_db
class TestPipelineDefinition:
    """Tests for PipelineDefinition model."""

    def test_create_minimal_definition(self):
        """Test creating a minimal pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-pipeline",
            description="Test pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    }
                ],
            },
        )
        assert definition.id is not None
        assert definition.name == "test-pipeline"
        assert definition.is_active is True
        assert definition.version == 1

    def test_unique_name_constraint(self):
        """Test that pipeline names must be unique."""
        PipelineDefinition.objects.create(
            name="unique-test",
            config={"version": "1.0", "nodes": []},
        )
        with pytest.raises(Exception):  # IntegrityError
            PipelineDefinition.objects.create(
                name="unique-test",
                config={"version": "1.0", "nodes": []},
            )

    def test_str_representation(self):
        """Test string representation."""
        definition = PipelineDefinition.objects.create(
            name="my-pipeline",
            config={"version": "1.0", "nodes": []},
        )
        assert "my-pipeline" in str(definition)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_pipeline_definition.py -v`
Expected: FAIL with "cannot import name 'PipelineDefinition'"

**Step 3: Write minimal implementation**

Add to `apps/orchestration/models.py`:

```python
class PipelineDefinition(models.Model):
    """
    Reusable pipeline definition.

    Stores the configuration for a pipeline as a JSON schema,
    allowing dynamic pipeline construction and execution.

    Example config:
    {
        "version": "1.0",
        "description": "Analyze and notify",
        "defaults": {
            "max_retries": 3,
            "timeout_seconds": 300
        },
        "nodes": [
            {
                "id": "analyze_openai",
                "type": "intelligence",
                "config": {"provider": "openai"},
                "next": "analyze_claude"
            },
            {
                "id": "analyze_claude",
                "type": "intelligence",
                "config": {"provider": "claude"},
                "next": "notify_slack"
            },
            {
                "id": "notify_slack",
                "type": "notify",
                "config": {"driver": "slack"}
            }
        ]
    }
    """

    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique identifier for this pipeline definition.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Human-readable description of what this pipeline does.",
    )
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number, incremented on each update.",
    )
    config = models.JSONField(
        default=dict,
        help_text="Pipeline configuration schema (nodes, connections, defaults).",
    )
    tags = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary tags for filtering/categorization.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this pipeline can be executed.",
    )
    created_by = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="User or system that created this definition.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["name", "is_active"]),
            models.Index(fields=["is_active", "-updated_at"]),
        ]

    def __str__(self):
        return f"{self.name} (v{self.version})"

    def get_nodes(self) -> list[dict]:
        """Return the list of nodes from config."""
        return self.config.get("nodes", [])

    def get_defaults(self) -> dict:
        """Return default settings from config."""
        return self.config.get("defaults", {})

    def get_entry_node(self) -> dict | None:
        """Return the first node (entry point) of the pipeline."""
        nodes = self.get_nodes()
        return nodes[0] if nodes else None
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_pipeline_definition.py -v`
Expected: PASS

**Step 5: Create and run migration**

Run: `uv run python manage.py makemigrations orchestration --name add_pipeline_definition`
Run: `uv run python manage.py migrate`
Expected: Migration created and applied

**Step 6: Commit**

```bash
git add apps/orchestration/models.py apps/orchestration/migrations/ apps/orchestration/_tests/
git commit -m "feat(orchestration): add PipelineDefinition model for reusable pipelines"
```

---

### Task 2: Create _tests directory structure for orchestration

**Files:**
- Create: `apps/orchestration/_tests/__init__.py`
- Create: `apps/orchestration/_tests/conftest.py`
- Move: `apps/orchestration/_tests/test_pipeline_definition.py` (already created)

**Step 1: Create directory structure**

```bash
mkdir -p apps/orchestration/_tests
```

**Step 2: Create __init__.py**

```python
# apps/orchestration/_tests/__init__.py
"""Orchestration app test suite."""
```

**Step 3: Create conftest.py**

```python
# apps/orchestration/_tests/conftest.py
"""Shared test fixtures for orchestration app."""

import pytest

from apps.orchestration.models import PipelineDefinition


@pytest.fixture
def simple_pipeline_config():
    """A simple pipeline configuration for testing."""
    return {
        "version": "1.0",
        "description": "Simple test pipeline",
        "defaults": {
            "max_retries": 3,
            "timeout_seconds": 300,
        },
        "nodes": [
            {
                "id": "analyze",
                "type": "intelligence",
                "config": {"provider": "local"},
                "next": "notify",
            },
            {
                "id": "notify",
                "type": "notify",
                "config": {"driver": "generic"},
            },
        ],
    }


@pytest.fixture
def chained_ai_pipeline_config():
    """Pipeline config that chains multiple AI providers."""
    return {
        "version": "1.0",
        "description": "Chain OpenAI to notify",
        "nodes": [
            {
                "id": "gather_context",
                "type": "context",
                "config": {"include": ["cpu", "memory", "disk"]},
                "next": "analyze_openai",
            },
            {
                "id": "analyze_openai",
                "type": "intelligence",
                "config": {"provider": "openai"},
                "next": "notify_slack",
            },
            {
                "id": "notify_slack",
                "type": "notify",
                "config": {"driver": "slack"},
            },
        ],
    }


@pytest.fixture
def pipeline_definition(db, simple_pipeline_config):
    """Create a PipelineDefinition instance."""
    return PipelineDefinition.objects.create(
        name="test-pipeline",
        description="Test pipeline",
        config=simple_pipeline_config,
    )
```

**Step 4: Commit**

```bash
git add apps/orchestration/_tests/
git commit -m "chore(orchestration): create _tests directory structure"
```

---

### Task 3: Create NodeType enum and node type registry

**Files:**
- Create: `apps/orchestration/nodes/__init__.py`
- Create: `apps/orchestration/nodes/base.py`

**Step 1: Write the failing test**

Create file `apps/orchestration/_tests/test_nodes.py`:

```python
# apps/orchestration/_tests/test_nodes.py
"""Tests for pipeline node types."""

import pytest

from apps.orchestration.nodes import NodeType, get_node_handler, list_node_types


class TestNodeRegistry:
    """Tests for node type registry."""

    def test_list_node_types(self):
        """Test listing available node types."""
        types = list_node_types()
        assert "intelligence" in types
        assert "notify" in types
        assert "context" in types

    def test_get_node_handler(self):
        """Test getting a node handler by type."""
        from apps.orchestration.nodes.base import BaseNodeHandler

        handler = get_node_handler("intelligence")
        assert isinstance(handler, BaseNodeHandler)

    def test_get_unknown_handler_raises(self):
        """Test that unknown node type raises KeyError."""
        with pytest.raises(KeyError):
            get_node_handler("nonexistent_type")


class TestNodeType:
    """Tests for NodeType enum."""

    def test_intelligence_type(self):
        """Test intelligence node type exists."""
        assert NodeType.INTELLIGENCE.value == "intelligence"

    def test_notify_type(self):
        """Test notify node type exists."""
        assert NodeType.NOTIFY.value == "notify"

    def test_context_type(self):
        """Test context node type exists."""
        assert NodeType.CONTEXT.value == "context"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py -v`
Expected: FAIL with "No module named 'apps.orchestration.nodes'"

**Step 3: Create nodes package**

```bash
mkdir -p apps/orchestration/nodes
```

**Step 4: Write base.py**

```python
# apps/orchestration/nodes/base.py
"""Base node handler and types for pipeline nodes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    payload: dict[str, Any] = field(default_factory=dict)
    previous_outputs: dict[str, Any] = field(default_factory=dict)
    environment: str = "production"
    source: str = "unknown"

    def get_previous(self, node_id: str) -> dict[str, Any]:
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
    output: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "output": self.output,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


class BaseNodeHandler(ABC):
    """
    Abstract base class for pipeline node handlers.

    Each node type implements this interface to process
    data in the pipeline.
    """

    node_type: NodeType = NodeType.CONTEXT
    name: str = "base"

    @abstractmethod
    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """
        Execute the node with given context and configuration.

        Args:
            ctx: Node context with previous outputs and metadata.
            config: Node-specific configuration from pipeline definition.

        Returns:
            NodeResult with output data and any errors.
        """
        raise NotImplementedError

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """
        Validate the node configuration.

        Args:
            config: Node configuration to validate.

        Returns:
            List of validation error messages (empty if valid).
        """
        return []
```

**Step 5: Write __init__.py with registry**

```python
# apps/orchestration/nodes/__init__.py
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


__all__ = [
    "BaseNodeHandler",
    "NodeContext",
    "NodeResult",
    "NodeType",
    "get_node_handler",
    "list_node_types",
    "register_node_handler",
]
```

**Step 6: Run test to verify it partially passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestNodeType -v`
Expected: PASS for enum tests, registry tests will fail (no handlers yet)

**Step 7: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add pipeline node base types and registry"
```

---

### Task 4: Create IntelligenceNodeHandler

**Files:**
- Create: `apps/orchestration/nodes/intelligence.py`
- Modify: `apps/orchestration/nodes/__init__.py`

**Step 1: Write the failing test**

Add to `apps/orchestration/_tests/test_nodes.py`:

```python
class TestIntelligenceNodeHandler:
    """Tests for IntelligenceNodeHandler."""

    def test_execute_with_local_provider(self):
        """Test executing intelligence node with local provider."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("intelligence")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
            payload={"test": "data"},
        )
        config = {"provider": "local"}

        result = handler.execute(ctx, config)

        assert result.node_type == "intelligence"
        assert not result.has_errors
        assert "recommendations" in result.output

    def test_validate_config_missing_provider(self):
        """Test validation fails without provider."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("intelligence")
        errors = handler.validate_config({})

        assert len(errors) > 0
        assert any("provider" in e.lower() for e in errors)

    def test_validate_config_valid(self):
        """Test validation passes with provider."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("intelligence")
        errors = handler.validate_config({"provider": "local"})

        assert len(errors) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestIntelligenceNodeHandler -v`
Expected: FAIL

**Step 3: Write the intelligence handler**

```python
# apps/orchestration/nodes/intelligence.py
"""Intelligence node handler for AI analysis."""

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


class IntelligenceNodeHandler(BaseNodeHandler):
    """
    Node handler for intelligence/AI analysis.

    Uses registered intelligence providers to analyze
    context and generate recommendations.
    """

    node_type = NodeType.INTELLIGENCE
    name = "intelligence"

    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """Execute AI analysis using configured provider."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "intelligence"),
            node_type="intelligence",
        )

        try:
            from apps.intelligence.providers import get_provider

            provider_name = config.get("provider", "local")
            provider_config = config.get("provider_config", {})

            provider = get_provider(provider_name, **provider_config)

            # Check if we have an incident to analyze
            recommendations = []
            if ctx.incident_id:
                from apps.alerts.models import Incident

                incident = Incident.objects.filter(id=ctx.incident_id).first()
                if incident:
                    recommendations = provider.analyze(incident)
                else:
                    recommendations = provider.get_recommendations()
            else:
                recommendations = provider.get_recommendations()

            # Convert recommendations to dicts
            recs_list = []
            for r in recommendations:
                if hasattr(r, "to_dict"):
                    recs_list.append(r.to_dict())
                elif isinstance(r, dict):
                    recs_list.append(r)
                else:
                    recs_list.append(vars(r) if hasattr(r, "__dict__") else {"value": str(r)})

            result.output = {
                "provider": provider_name,
                "recommendations": recs_list,
                "count": len(recs_list),
            }

            # Add summary from first recommendation if available
            if recs_list:
                first = recs_list[0]
                result.output["summary"] = first.get("title", "")
                result.output["description"] = first.get("description", "")

        except Exception as e:
            logger.exception(f"Error in IntelligenceNodeHandler: {e}")
            result.errors.append(f"Intelligence error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate intelligence node configuration."""
        errors = []

        if "provider" not in config:
            errors.append("Missing required field: provider")
        else:
            # Check if provider exists
            from apps.intelligence.providers import PROVIDERS

            if config["provider"] not in PROVIDERS:
                errors.append(
                    f"Unknown provider: {config['provider']}. "
                    f"Available: {list(PROVIDERS.keys())}"
                )

        return errors
```

**Step 4: Register handler in __init__.py**

Update `apps/orchestration/nodes/__init__.py`:

```python
# Add at the end of the file, after imports

# Import and register node handlers
from apps.orchestration.nodes.intelligence import IntelligenceNodeHandler

register_node_handler("intelligence", IntelligenceNodeHandler)
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestIntelligenceNodeHandler -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add IntelligenceNodeHandler for AI analysis nodes"
```

---

### Task 5: Create NotifyNodeHandler

**Files:**
- Create: `apps/orchestration/nodes/notify.py`
- Modify: `apps/orchestration/nodes/__init__.py`

**Step 1: Write the failing test**

Add to `apps/orchestration/_tests/test_nodes.py`:

```python
class TestNotifyNodeHandler:
    """Tests for NotifyNodeHandler."""

    def test_execute_with_generic_driver(self):
        """Test executing notify node with generic driver."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("notify")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
            previous_outputs={
                "analyze": {
                    "recommendations": [
                        {"title": "Test", "description": "Test desc", "priority": "high"}
                    ],
                }
            },
        )
        # Generic driver with mock endpoint
        config = {
            "driver": "generic",
            "driver_config": {
                "endpoint": "https://httpbin.org/post",
                "method": "POST",
            },
        }

        result = handler.execute(ctx, config)

        assert result.node_type == "notify"
        # Note: actual send may fail in test env, but structure should be correct
        assert "driver" in result.output

    def test_validate_config_missing_driver(self):
        """Test validation fails without driver."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("notify")
        errors = handler.validate_config({})

        assert len(errors) > 0
        assert any("driver" in e.lower() for e in errors)

    def test_validate_config_valid(self):
        """Test validation passes with driver."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("notify")
        errors = handler.validate_config({"driver": "generic"})

        assert len(errors) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestNotifyNodeHandler -v`
Expected: FAIL

**Step 3: Write the notify handler**

```python
# apps/orchestration/nodes/notify.py
"""Notify node handler for notification dispatch."""

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


class NotifyNodeHandler(BaseNodeHandler):
    """
    Node handler for notification dispatch.

    Uses registered notification drivers to send
    messages to various channels.
    """

    node_type = NodeType.NOTIFY
    name = "notify"

    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """Execute notification using configured driver."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "notify"),
            node_type="notify",
        )

        try:
            from apps.notify.drivers.base import NotificationMessage
            from apps.notify.views import DRIVER_REGISTRY

            driver_name = config.get("driver", "generic")
            driver_config = config.get("driver_config", {})
            channel = config.get("channel", "default")

            # Get driver class
            if driver_name not in DRIVER_REGISTRY:
                result.errors.append(
                    f"Unknown driver: {driver_name}. Available: {list(DRIVER_REGISTRY.keys())}"
                )
                return result

            driver_cls = DRIVER_REGISTRY[driver_name]
            driver = driver_cls()

            # Build message from previous node outputs
            message_title = config.get("title", "Pipeline Notification")
            message_body = config.get("message", "")
            severity = config.get("severity", "info")

            # Try to get content from previous intelligence node
            for node_id, output in ctx.previous_outputs.items():
                if isinstance(output, dict) and "recommendations" in output:
                    recs = output.get("recommendations", [])
                    if recs:
                        first_rec = recs[0]
                        message_title = first_rec.get("title", message_title)
                        message_body = first_rec.get("description", message_body)
                        # Map priority to severity
                        priority = first_rec.get("priority", "").lower()
                        if priority in ("critical", "high"):
                            severity = "warning" if priority == "high" else "critical"
                    break

            message = NotificationMessage(
                title=message_title,
                message=message_body or "No message content",
                severity=severity,
                channel=channel,
                tags={
                    "trace_id": ctx.trace_id,
                    "run_id": ctx.run_id,
                    "pipeline_node": config.get("id", "notify"),
                },
                context={
                    "incident_id": ctx.incident_id,
                    "source": ctx.source,
                    "environment": ctx.environment,
                    "previous_outputs": ctx.previous_outputs,
                },
            )

            # Validate and send
            if not driver.validate_config(driver_config):
                result.errors.append(f"Invalid driver configuration for: {driver_name}")
                result.output = {"driver": driver_name, "status": "failed"}
                return result

            try:
                send_result = driver.send(message, driver_config)
                success = send_result.get("success", False)
                result.output = {
                    "driver": driver_name,
                    "channel": channel,
                    "status": "success" if success else "failed",
                    "message_id": send_result.get("message_id", ""),
                    "response": send_result,
                }
                if not success:
                    result.errors.append(
                        f"Send failed: {send_result.get('error', 'Unknown error')}"
                    )
            except Exception as e:
                logger.exception(f"Error sending notification: {e}")
                result.errors.append(f"Send error: {str(e)}")
                result.output = {"driver": driver_name, "status": "failed"}

        except Exception as e:
            logger.exception(f"Error in NotifyNodeHandler: {e}")
            result.errors.append(f"Notify error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate notify node configuration."""
        errors = []

        if "driver" not in config:
            errors.append("Missing required field: driver")
        else:
            from apps.notify.views import DRIVER_REGISTRY

            if config["driver"] not in DRIVER_REGISTRY:
                errors.append(
                    f"Unknown driver: {config['driver']}. "
                    f"Available: {list(DRIVER_REGISTRY.keys())}"
                )

        return errors
```

**Step 4: Register handler in __init__.py**

Update `apps/orchestration/nodes/__init__.py` to add:

```python
from apps.orchestration.nodes.notify import NotifyNodeHandler

register_node_handler("notify", NotifyNodeHandler)
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestNotifyNodeHandler -v`
Expected: PASS (or partial pass - send may fail without real endpoint)

**Step 6: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add NotifyNodeHandler for notification nodes"
```

---

### Task 6: Create ContextNodeHandler

**Files:**
- Create: `apps/orchestration/nodes/context.py`
- Modify: `apps/orchestration/nodes/__init__.py`

**Step 1: Write the failing test**

Add to `apps/orchestration/_tests/test_nodes.py`:

```python
class TestContextNodeHandler:
    """Tests for ContextNodeHandler."""

    def test_execute_gathers_system_info(self):
        """Test executing context node gathers system info."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("context")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
        )
        config = {"include": ["cpu", "memory"]}

        result = handler.execute(ctx, config)

        assert result.node_type == "context"
        assert not result.has_errors
        assert "system" in result.output

    def test_execute_includes_cpu_when_requested(self):
        """Test CPU metrics are included when requested."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        config = {"include": ["cpu"]}

        result = handler.execute(ctx, config)

        assert "cpu" in result.output.get("system", {})

    def test_execute_includes_memory_when_requested(self):
        """Test memory metrics are included when requested."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        config = {"include": ["memory"]}

        result = handler.execute(ctx, config)

        assert "memory" in result.output.get("system", {})
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestContextNodeHandler -v`
Expected: FAIL

**Step 3: Write the context handler**

```python
# apps/orchestration/nodes/context.py
"""Context node handler for gathering system information."""

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


class ContextNodeHandler(BaseNodeHandler):
    """
    Node handler for gathering system context.

    Collects system metrics (CPU, memory, disk) to provide
    context for downstream nodes like intelligence providers.
    """

    node_type = NodeType.CONTEXT
    name = "context"

    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """Gather system context based on configuration."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "context"),
            node_type="context",
        )

        try:
            import psutil

            include = config.get("include", ["cpu", "memory", "disk"])
            system_info: dict[str, Any] = {}

            if "cpu" in include:
                system_info["cpu"] = {
                    "percent": psutil.cpu_percent(interval=0.1),
                    "count": psutil.cpu_count(),
                    "count_logical": psutil.cpu_count(logical=True),
                }
                # Get per-CPU percentages
                try:
                    system_info["cpu"]["per_cpu"] = psutil.cpu_percent(
                        interval=0.1, percpu=True
                    )
                except Exception:
                    pass

            if "memory" in include:
                mem = psutil.virtual_memory()
                system_info["memory"] = {
                    "total_gb": round(mem.total / (1024**3), 2),
                    "available_gb": round(mem.available / (1024**3), 2),
                    "used_gb": round(mem.used / (1024**3), 2),
                    "percent": mem.percent,
                }

            if "disk" in include:
                disk_path = config.get("disk_path", "/")
                try:
                    disk = psutil.disk_usage(disk_path)
                    system_info["disk"] = {
                        "path": disk_path,
                        "total_gb": round(disk.total / (1024**3), 2),
                        "used_gb": round(disk.used / (1024**3), 2),
                        "free_gb": round(disk.free / (1024**3), 2),
                        "percent": disk.percent,
                    }
                except Exception as e:
                    system_info["disk"] = {"error": str(e)}

            if "processes" in include:
                top_n = config.get("top_processes", 5)
                processes = []
                for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                    try:
                        info = proc.info
                        processes.append({
                            "pid": info["pid"],
                            "name": info["name"],
                            "cpu_percent": info["cpu_percent"],
                            "memory_percent": info["memory_percent"],
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                # Sort by memory usage
                processes.sort(key=lambda p: p.get("memory_percent", 0) or 0, reverse=True)
                system_info["processes"] = processes[:top_n]

            result.output = {
                "system": system_info,
                "collected_at": time.time(),
                "metrics_included": include,
            }

        except ImportError:
            result.errors.append("psutil not installed - cannot gather system metrics")
        except Exception as e:
            logger.exception(f"Error in ContextNodeHandler: {e}")
            result.errors.append(f"Context error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate context node configuration."""
        errors = []
        include = config.get("include", [])

        valid_metrics = {"cpu", "memory", "disk", "processes"}
        invalid = set(include) - valid_metrics
        if invalid:
            errors.append(f"Invalid metrics: {invalid}. Valid: {valid_metrics}")

        return errors
```

**Step 4: Register handler in __init__.py**

Update `apps/orchestration/nodes/__init__.py` to add:

```python
from apps.orchestration.nodes.context import ContextNodeHandler

register_node_handler("context", ContextNodeHandler)
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestContextNodeHandler -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add ContextNodeHandler for system metrics gathering"
```

---

### Task 7: Create IngestNodeHandler (Alert Trigger)

**Files:**
- Create: `apps/orchestration/nodes/ingest.py`
- Modify: `apps/orchestration/nodes/__init__.py`

**Step 1: Write the failing test**

Add to `apps/orchestration/_tests/test_nodes.py`:

```python
class TestIngestNodeHandler:
    """Tests for IngestNodeHandler."""

    def test_execute_creates_incident(self):
        """Test executing ingest node creates an incident."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("ingest")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
            payload={
                "driver": "generic",
                "payload": {
                    "title": "Test Alert",
                    "severity": "warning",
                    "description": "Test description",
                },
            },
        )
        config = {}

        result = handler.execute(ctx, config)

        assert result.node_type == "ingest"
        # Should have incident info in output
        assert "incident_id" in result.output or "alerts_created" in result.output

    def test_execute_with_alertmanager_payload(self):
        """Test ingest node with Alertmanager-style payload."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("ingest")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
            payload={
                "driver": "alertmanager",
                "payload": {
                    "alerts": [
                        {
                            "status": "firing",
                            "labels": {"alertname": "HighMemory", "severity": "warning"},
                            "annotations": {"description": "Memory usage high"},
                        }
                    ],
                },
            },
        )
        config = {"driver": "alertmanager"}

        result = handler.execute(ctx, config)

        assert result.node_type == "ingest"
        assert not result.has_errors or "alerts_created" in result.output

    def test_validate_config_always_valid(self):
        """Test ingest node config validation (minimal requirements)."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("ingest")
        errors = handler.validate_config({})

        # Ingest has no required config
        assert len(errors) == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestIngestNodeHandler -v`
Expected: FAIL

**Step 3: Write the ingest handler**

```python
# apps/orchestration/nodes/ingest.py
"""Ingest node handler for alert ingestion."""

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


class IngestNodeHandler(BaseNodeHandler):
    """
    Node handler for alert ingestion.

    Wraps the existing AlertOrchestrator to parse incoming
    alert payloads and create/update Incidents and Alerts.

    This is the entry point for alert-triggered pipelines.
    """

    node_type = NodeType.INGEST
    name = "ingest"

    def execute(self, ctx: NodeContext, config: dict[str, Any]) -> NodeResult:
        """Execute alert ingestion."""
        start_time = time.perf_counter()
        result = NodeResult(
            node_id=config.get("id", "ingest"),
            node_type="ingest",
        )

        try:
            from apps.alerts.services import AlertOrchestrator

            payload = ctx.payload
            driver = payload.get("driver") or config.get("driver")
            alert_payload = payload.get("payload", payload)

            # Validate payload
            if not isinstance(alert_payload, dict):
                result.errors.append("payload must be a JSON object")
                result.duration_ms = (time.perf_counter() - start_time) * 1000
                return result

            # Process the webhook
            orchestrator = AlertOrchestrator()
            proc_result = orchestrator.process_webhook(
                alert_payload,
                driver=driver,
            )

            # Populate result
            result.output = {
                "alerts_created": proc_result.alerts_created,
                "alerts_updated": proc_result.alerts_updated,
                "alerts_resolved": proc_result.alerts_resolved,
                "incidents_created": proc_result.incidents_created,
                "incidents_updated": proc_result.incidents_updated,
                "source": ctx.source,
            }

            # Copy errors from processing
            if proc_result.errors:
                result.errors.extend(proc_result.errors)

            # Find the incident ID from the latest alert
            from apps.alerts.models import Alert

            latest_alert = (
                Alert.objects.order_by("-received_at").select_related("incident").first()
            )
            if latest_alert and latest_alert.incident_id:
                result.output["incident_id"] = latest_alert.incident_id
                result.output["alert_fingerprint"] = latest_alert.fingerprint
                result.output["severity"] = latest_alert.severity

                # IMPORTANT: Update the context so subsequent nodes have incident_id
                # This is done by the orchestrator reading from output

        except Exception as e:
            logger.exception(f"Error in IngestNodeHandler: {e}")
            result.errors.append(f"Ingest error: {str(e)}")

        result.duration_ms = (time.perf_counter() - start_time) * 1000
        return result

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate ingest node configuration."""
        # Ingest node has no required config - driver can come from payload
        return []
```

**Step 4: Register handler in __init__.py**

Update `apps/orchestration/nodes/__init__.py` to add:

```python
from apps.orchestration.nodes.ingest import IngestNodeHandler

register_node_handler("ingest", IngestNodeHandler)
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestIngestNodeHandler -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add IngestNodeHandler for alert-triggered pipelines"
```

---

### Task 8: Create DefinitionBasedOrchestrator

**Files:**
- Create: `apps/orchestration/definition_orchestrator.py`

**Step 1: Write the failing test**

Create file `apps/orchestration/_tests/test_definition_orchestrator.py`:

```python
# apps/orchestration/_tests/test_definition_orchestrator.py
"""Tests for DefinitionBasedOrchestrator."""

import pytest

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition


@pytest.mark.django_db
class TestDefinitionBasedOrchestrator:
    """Tests for DefinitionBasedOrchestrator."""

    def test_execute_simple_pipeline(self, simple_pipeline_config):
        """Test executing a simple pipeline."""
        definition = PipelineDefinition.objects.create(
            name="test-simple",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        assert result["status"] in ("completed", "partial")
        assert "executed_nodes" in result
        assert len(result["executed_nodes"]) > 0

    def test_execute_records_pipeline_run(self, simple_pipeline_config):
        """Test that execution creates a PipelineRun record."""
        from apps.orchestration.models import PipelineRun

        definition = PipelineDefinition.objects.create(
            name="test-record",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        # Check PipelineRun was created
        run = PipelineRun.objects.filter(run_id=result["run_id"]).first()
        assert run is not None
        assert run.source == "test"

    def test_execute_chains_node_outputs(self, simple_pipeline_config):
        """Test that node outputs are passed to subsequent nodes."""
        definition = PipelineDefinition.objects.create(
            name="test-chain",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(
            payload={"test": "data"},
            source="test",
        )

        # Check that node results include outputs
        assert "node_results" in result
        for node_id, node_result in result["node_results"].items():
            assert "output" in node_result

    def test_validate_definition(self, simple_pipeline_config):
        """Test validating a pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-validate",
            config=simple_pipeline_config,
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert isinstance(errors, list)

    def test_validate_catches_invalid_node_type(self):
        """Test validation catches invalid node types."""
        definition = PipelineDefinition.objects.create(
            name="test-invalid",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "bad", "type": "nonexistent"},
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        assert len(errors) > 0
        assert any("nonexistent" in e.lower() for e in errors)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_definition_orchestrator.py -v`
Expected: FAIL with "No module named 'apps.orchestration.definition_orchestrator'"

**Step 3: Write the orchestrator**

```python
# apps/orchestration/definition_orchestrator.py
"""
Definition-based pipeline orchestrator.

Executes pipelines based on PipelineDefinition configurations,
allowing dynamic, configurable workflows.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from django.db import transaction

from apps.orchestration.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineStatus,
    StageExecution,
    StageStatus,
)
from apps.orchestration.nodes import (
    NodeContext,
    NodeResult,
    get_node_handler,
    list_node_types,
)

logger = logging.getLogger(__name__)


class DefinitionBasedOrchestrator:
    """
    Orchestrator that executes pipelines based on PipelineDefinition.

    Unlike the hardcoded PipelineOrchestrator, this class dynamically
    constructs and executes pipelines from JSON configurations.

    Usage:
        definition = PipelineDefinition.objects.get(name="my-pipeline")
        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={"key": "value"}, source="api")
    """

    def __init__(self, definition: PipelineDefinition):
        """
        Initialize with a pipeline definition.

        Args:
            definition: The PipelineDefinition to execute.
        """
        self.definition = definition
        self.config = definition.config
        self.defaults = definition.get_defaults()

    def validate(self) -> list[str]:
        """
        Validate the pipeline definition.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        # Check version
        if "version" not in self.config:
            errors.append("Missing 'version' in config")

        # Check nodes
        nodes = self.definition.get_nodes()
        if not nodes:
            errors.append("Pipeline has no nodes defined")

        available_types = list_node_types()
        node_ids = set()

        for i, node in enumerate(nodes):
            # Check required fields
            if "id" not in node:
                errors.append(f"Node {i} missing 'id'")
            else:
                if node["id"] in node_ids:
                    errors.append(f"Duplicate node id: {node['id']}")
                node_ids.add(node["id"])

            if "type" not in node:
                errors.append(f"Node {i} missing 'type'")
            elif node["type"] not in available_types:
                errors.append(
                    f"Node {node.get('id', i)} has unknown type: {node['type']}. "
                    f"Available: {available_types}"
                )
            else:
                # Validate node-specific config
                try:
                    handler = get_node_handler(node["type"])
                    node_config = node.get("config", {})
                    node_errors = handler.validate_config(node_config)
                    for e in node_errors:
                        errors.append(f"Node {node.get('id', i)}: {e}")
                except Exception as e:
                    errors.append(f"Node {node.get('id', i)}: validation error - {e}")

            # Validate 'next' references
            next_node = node.get("next")
            if next_node and next_node not in node_ids:
                # Check if it's defined later
                later_ids = {n.get("id") for n in nodes[i + 1 :]}
                if next_node not in later_ids:
                    errors.append(
                        f"Node {node.get('id', i)} references unknown next node: {next_node}"
                    )

        return errors

    def execute(
        self,
        payload: dict[str, Any],
        source: str = "unknown",
        trace_id: str | None = None,
        environment: str = "production",
        incident_id: int | None = None,
    ) -> dict[str, Any]:
        """
        Execute the pipeline.

        Args:
            payload: Input data for the pipeline.
            source: Source system identifier.
            trace_id: Optional trace ID for correlation.
            environment: Environment name.
            incident_id: Optional incident ID for context.

        Returns:
            Dictionary with execution results.
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())

        start_time = time.perf_counter()

        # Create pipeline run record
        with transaction.atomic():
            pipeline_run = PipelineRun.objects.create(
                trace_id=trace_id,
                run_id=run_id,
                source=source,
                environment=environment,
                status=PipelineStatus.PENDING,
                incident_id=incident_id,
                max_retries=self.defaults.get("max_retries", 3),
            )

        logger.info(
            f"Starting definition-based pipeline: {self.definition.name}",
            extra={
                "trace_id": trace_id,
                "run_id": run_id,
                "definition": self.definition.name,
            },
        )

        # Initialize execution context
        node_ctx = NodeContext(
            trace_id=trace_id,
            run_id=run_id,
            incident_id=incident_id,
            payload=payload,
            environment=environment,
            source=source,
        )

        # Execute nodes
        nodes = self.definition.get_nodes()
        executed_nodes: list[str] = []
        skipped_nodes: list[str] = []
        node_results: dict[str, dict[str, Any]] = {}
        final_status = "completed"
        final_error = None

        try:
            pipeline_run.mark_started(nodes[0]["type"] if nodes else "unknown")

            for node_config in nodes:
                node_id = node_config["id"]
                node_type = node_config["type"]

                # Check skip conditions
                if self._should_skip(node_config, node_results):
                    skipped_nodes.append(node_id)
                    logger.info(
                        f"Skipping node {node_id}",
                        extra={"trace_id": trace_id, "node_id": node_id},
                    )
                    continue

                # Execute node
                try:
                    result = self._execute_node(
                        pipeline_run=pipeline_run,
                        node_ctx=node_ctx,
                        node_config=node_config,
                    )

                    node_results[node_id] = result.to_dict()
                    executed_nodes.append(node_id)

                    # Update context with this node's output
                    node_ctx.previous_outputs[node_id] = result.output

                    # IMPORTANT: If ingest node created an incident, propagate incident_id
                    # to subsequent nodes so they have context
                    if node_type == "ingest" and result.output.get("incident_id"):
                        node_ctx.incident_id = result.output["incident_id"]
                        pipeline_run.incident_id = result.output["incident_id"]
                        pipeline_run.save(update_fields=["incident_id", "updated_at"])
                        logger.info(
                            f"Ingest node created incident_id={node_ctx.incident_id}",
                            extra={"trace_id": trace_id, "incident_id": node_ctx.incident_id},
                        )

                    # Check for errors
                    if result.has_errors:
                        required = node_config.get("required", True)
                        if required:
                            final_status = "failed"
                            final_error = f"Node {node_id} failed: {'; '.join(result.errors)}"
                            break
                        else:
                            logger.warning(
                                f"Non-required node {node_id} failed, continuing",
                                extra={"trace_id": trace_id, "errors": result.errors},
                            )

                except Exception as e:
                    logger.exception(f"Error executing node {node_id}")
                    node_results[node_id] = {
                        "node_id": node_id,
                        "node_type": node_type,
                        "errors": [str(e)],
                    }
                    required = node_config.get("required", True)
                    if required:
                        final_status = "failed"
                        final_error = f"Node {node_id} error: {str(e)}"
                        break

            # Mark pipeline complete
            duration_ms = (time.perf_counter() - start_time) * 1000

            if final_status == "completed":
                pipeline_run.mark_completed(PipelineStatus.NOTIFIED)
            else:
                pipeline_run.mark_failed(
                    error_type="NodeExecutionError",
                    message=final_error or "Unknown error",
                    retryable=True,
                )

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            final_status = "failed"
            final_error = str(e)
            pipeline_run.mark_failed(
                error_type=type(e).__name__,
                message=str(e),
                retryable=True,
            )
            logger.exception(f"Pipeline execution failed: {e}")

        return {
            "trace_id": trace_id,
            "run_id": run_id,
            "definition": self.definition.name,
            "definition_version": self.definition.version,
            "status": final_status,
            "incident_id": node_ctx.incident_id,  # May be set by ingest node
            "executed_nodes": executed_nodes,
            "skipped_nodes": skipped_nodes,
            "node_results": node_results,
            "duration_ms": duration_ms,
            "error": final_error,
        }

    def _execute_node(
        self,
        pipeline_run: PipelineRun,
        node_ctx: NodeContext,
        node_config: dict[str, Any],
    ) -> NodeResult:
        """Execute a single node."""
        node_id = node_config["id"]
        node_type = node_config["type"]
        config = node_config.get("config", {})
        config["id"] = node_id  # Ensure ID is in config

        # Create stage execution record
        stage_execution = StageExecution.objects.create(
            pipeline_run=pipeline_run,
            stage=node_type[:20],  # Truncate to fit field
            attempt=1,
            idempotency_key=f"{pipeline_run.run_id}:{node_id}:1",
            status=StageStatus.PENDING,
        )

        stage_execution.mark_started()

        try:
            handler = get_node_handler(node_type)
            result = handler.execute(node_ctx, config)

            if result.has_errors:
                stage_execution.mark_failed(
                    error_type="NodeError",
                    error_message="; ".join(result.errors),
                    retryable=True,
                )
            else:
                stage_execution.mark_succeeded(output_snapshot=result.to_dict())

            return result

        except Exception as e:
            stage_execution.mark_failed(
                error_type=type(e).__name__,
                error_message=str(e),
                retryable=True,
            )
            raise

    def _should_skip(
        self,
        node_config: dict[str, Any],
        previous_results: dict[str, dict[str, Any]],
    ) -> bool:
        """
        Determine if a node should be skipped.

        Args:
            node_config: The node configuration.
            previous_results: Results from previously executed nodes.

        Returns:
            True if node should be skipped.
        """
        # Check skip_if_errors - skip if specified nodes had errors
        skip_if_errors = node_config.get("skip_if_errors", [])
        for prereq_id in skip_if_errors:
            prereq_result = previous_results.get(prereq_id, {})
            if prereq_result.get("errors"):
                return True

        # Check skip_if_condition (simple expression evaluation)
        condition = node_config.get("skip_if_condition")
        if condition:
            # Build evaluation context
            eval_ctx = {
                "previous": previous_results,
            }
            try:
                # Simple safe evaluation (no exec, only basic comparisons)
                # For now, support basic patterns like "node_id.has_errors"
                if ".has_errors" in condition:
                    node_ref = condition.replace(".has_errors", "")
                    if node_ref in previous_results:
                        return bool(previous_results[node_ref].get("errors"))
            except Exception:
                pass

        return False
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_definition_orchestrator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/orchestration/definition_orchestrator.py apps/orchestration/_tests/
git commit -m "feat(orchestration): add DefinitionBasedOrchestrator for dynamic pipelines"
```

---

### Task 9: Add PipelineDefinition admin

**Files:**
- Modify: `apps/orchestration/admin.py`

**Step 1: Write the failing test**

Create file `apps/orchestration/_tests/test_admin.py`:

```python
# apps/orchestration/_tests/test_admin.py
"""Tests for orchestration admin."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.orchestration.admin import PipelineDefinitionAdmin
from apps.orchestration.models import PipelineDefinition


@pytest.mark.django_db
class TestPipelineDefinitionAdmin:
    """Tests for PipelineDefinitionAdmin."""

    def test_admin_registered(self):
        """Test PipelineDefinitionAdmin is registered."""
        from django.contrib import admin

        assert PipelineDefinition in admin.site._registry

    def test_list_display_fields(self):
        """Test list display includes key fields."""
        site = AdminSite()
        admin_instance = PipelineDefinitionAdmin(PipelineDefinition, site)

        assert "name" in admin_instance.list_display
        assert "is_active" in admin_instance.list_display
        assert "version" in admin_instance.list_display

    def test_can_create_via_admin(self):
        """Test creating a definition through admin."""
        from django.contrib.auth.models import User

        user = User.objects.create_superuser("admin", "admin@test.com", "password")

        from django.test import Client

        client = Client()
        client.force_login(user)

        response = client.get("/admin/orchestration/pipelinedefinition/add/")
        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py -v`
Expected: FAIL with "cannot import name 'PipelineDefinitionAdmin'"

**Step 3: Add admin configuration**

Update `apps/orchestration/admin.py`:

```python
"""Admin configuration for orchestration models."""

from django.contrib import admin
from django.utils.html import format_html

from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


class StageExecutionInline(admin.TabularInline):
    """Inline display of stage executions within a pipeline run."""

    model = StageExecution
    extra = 0
    readonly_fields = [
        "stage",
        "status",
        "attempt",
        "idempotency_key",
        "started_at",
        "completed_at",
        "duration_ms",
        "error_type",
        "error_message",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    """Admin for PipelineRun model."""

    list_display = [
        "run_id",
        "trace_id",
        "status",
        "source",
        "current_stage",
        "total_attempts",
        "created_at",
        "total_duration_ms",
    ]
    list_filter = ["status", "source", "current_stage", "environment"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
    readonly_fields = [
        "run_id",
        "trace_id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "total_duration_ms",
    ]
    inlines = [StageExecutionInline]

    fieldsets = [
        (
            "Identification",
            {
                "fields": [
                    "trace_id",
                    "run_id",
                    "incident",
                    "source",
                    "environment",
                    "alert_fingerprint",
                ]
            },
        ),
        (
            "State",
            {
                "fields": [
                    "status",
                    "current_stage",
                    "total_attempts",
                    "max_retries",
                ]
            },
        ),
        (
            "References",
            {
                "fields": [
                    "normalized_payload_ref",
                    "checker_output_ref",
                    "intelligence_output_ref",
                    "notify_output_ref",
                    "intelligence_fallback_used",
                ]
            },
        ),
        (
            "Errors",
            {
                "fields": [
                    "last_error_type",
                    "last_error_message",
                    "last_error_retryable",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                    "started_at",
                    "completed_at",
                    "total_duration_ms",
                ]
            },
        ),
    ]


@admin.register(StageExecution)
class StageExecutionAdmin(admin.ModelAdmin):
    """Admin for StageExecution model."""

    list_display = [
        "pipeline_run",
        "stage",
        "status",
        "attempt",
        "duration_ms",
        "started_at",
    ]
    list_filter = ["stage", "status"]
    search_fields = ["pipeline_run__run_id", "pipeline_run__trace_id", "idempotency_key"]
    readonly_fields = ["started_at", "completed_at", "duration_ms"]

    fieldsets = [
        (
            "Identification",
            {"fields": ["pipeline_run", "stage", "attempt", "idempotency_key"]},
        ),
        (
            "State",
            {"fields": ["status", "input_ref", "output_ref", "output_snapshot"]},
        ),
        (
            "Errors",
            {
                "fields": [
                    "error_type",
                    "error_message",
                    "error_stack",
                    "error_retryable",
                ],
                "classes": ["collapse"],
            },
        ),
        (
            "Timestamps",
            {"fields": ["started_at", "completed_at", "duration_ms"]},
        ),
    ]


@admin.register(PipelineDefinition)
class PipelineDefinitionAdmin(admin.ModelAdmin):
    """Admin for PipelineDefinition model."""

    list_display = [
        "name",
        "version",
        "is_active",
        "node_count",
        "tags_display",
        "created_by",
        "updated_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "description", "created_by"]
    readonly_fields = ["version", "created_at", "updated_at"]
    ordering = ["-updated_at"]

    fieldsets = [
        (
            "Identification",
            {
                "fields": ["name", "description", "is_active", "created_by"],
            },
        ),
        (
            "Configuration",
            {
                "fields": ["config"],
                "description": "Pipeline configuration in JSON format. See documentation for schema.",
            },
        ),
        (
            "Metadata",
            {
                "fields": ["tags", "version", "created_at", "updated_at"],
            },
        ),
    ]

    def node_count(self, obj):
        """Display the number of nodes in the pipeline."""
        nodes = obj.get_nodes()
        return len(nodes)

    node_count.short_description = "Nodes"

    def tags_display(self, obj):
        """Display tags in a readable format."""
        if not obj.tags:
            return "-"
        tags = obj.tags
        if isinstance(tags, dict):
            return ", ".join(f"{k}={v}" for k, v in tags.items())
        return str(tags)

    tags_display.short_description = "Tags"

    def save_model(self, request, obj, form, change):
        """Increment version on save if config changed."""
        if change and "config" in form.changed_data:
            obj.version += 1
        if not obj.created_by:
            obj.created_by = request.user.username
        super().save_model(request, obj, form, change)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/orchestration/admin.py apps/orchestration/_tests/
git commit -m "feat(orchestration): add PipelineDefinitionAdmin for managing pipelines"
```

---

### Task 10: Create API endpoints for pipeline definitions

**Files:**
- Modify: `apps/orchestration/views.py`
- Modify: `apps/orchestration/urls.py`

**Step 1: Write the failing test**

Create file `apps/orchestration/_tests/test_views.py`:

```python
# apps/orchestration/_tests/test_views.py
"""Tests for orchestration views."""

import json

import pytest
from django.test import Client

from apps.orchestration.models import PipelineDefinition


@pytest.mark.django_db
class TestPipelineDefinitionViews:
    """Tests for PipelineDefinition API endpoints."""

    def test_list_definitions(self):
        """Test listing pipeline definitions."""
        PipelineDefinition.objects.create(
            name="test-1",
            config={"version": "1.0", "nodes": []},
        )
        PipelineDefinition.objects.create(
            name="test-2",
            config={"version": "1.0", "nodes": []},
        )

        client = Client()
        response = client.get("/orchestration/definitions/")

        assert response.status_code == 200
        data = response.json()
        assert "definitions" in data
        assert len(data["definitions"]) >= 2

    def test_get_definition(self):
        """Test getting a specific definition."""
        definition = PipelineDefinition.objects.create(
            name="test-get",
            description="Test description",
            config={"version": "1.0", "nodes": []},
        )

        client = Client()
        response = client.get(f"/orchestration/definitions/{definition.name}/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-get"
        assert data["description"] == "Test description"

    def test_get_nonexistent_definition(self):
        """Test getting a definition that doesn't exist."""
        client = Client()
        response = client.get("/orchestration/definitions/nonexistent/")

        assert response.status_code == 404

    def test_validate_definition(self):
        """Test validating a pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-validate",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "analyze", "type": "intelligence", "config": {"provider": "local"}},
                ],
            },
        )

        client = Client()
        response = client.post(f"/orchestration/definitions/{definition.name}/validate/")

        assert response.status_code == 200
        data = response.json()
        assert "valid" in data
        assert "errors" in data

    def test_execute_definition(self, simple_pipeline_config):
        """Test executing a pipeline definition."""
        definition = PipelineDefinition.objects.create(
            name="test-execute",
            config=simple_pipeline_config,
        )

        client = Client()
        response = client.post(
            f"/orchestration/definitions/{definition.name}/execute/",
            data=json.dumps({"payload": {"test": "data"}, "source": "test"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data
        assert "status" in data
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_views.py -v`
Expected: FAIL

**Step 3: Update views.py**

Replace or update `apps/orchestration/views.py`:

```python
"""Views for orchestration API."""

import json
import logging

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition
from apps.orchestration.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


class JSONResponseMixin:
    """Mixin for JSON responses."""

    def json_response(self, data, status=200):
        return JsonResponse(data, status=status)

    def error_response(self, message, status=400):
        return JsonResponse({"error": message}, status=status)


@method_decorator(csrf_exempt, name="dispatch")
class PipelineWebhookView(JSONResponseMixin, View):
    """
    Webhook endpoint for triggering pipelines.

    POST /orchestration/pipeline/
        Triggers the default (hardcoded) pipeline.

    POST /orchestration/pipeline/?definition=<name>
        Triggers a specific pipeline definition.
    """

    def post(self, request):
        """Process incoming webhook and run pipeline."""
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        source = request.GET.get("source", payload.get("source", "webhook"))
        definition_name = request.GET.get("definition")

        try:
            if definition_name:
                # Use definition-based orchestrator
                try:
                    definition = PipelineDefinition.objects.get(
                        name=definition_name, is_active=True
                    )
                except PipelineDefinition.DoesNotExist:
                    return self.error_response(
                        f"Pipeline definition not found: {definition_name}", status=404
                    )

                orchestrator = DefinitionBasedOrchestrator(definition)
                result = orchestrator.execute(
                    payload=payload,
                    source=source,
                )
            else:
                # Use legacy hardcoded orchestrator
                orchestrator = PipelineOrchestrator()
                result = orchestrator.run_pipeline(
                    payload=payload,
                    source=source,
                )
                result = result.to_dict()

            return self.json_response(result)

        except Exception as e:
            logger.exception("Error executing pipeline")
            return self.error_response(f"Pipeline execution failed: {str(e)}", status=500)


@method_decorator(csrf_exempt, name="dispatch")
class PipelineDefinitionListView(JSONResponseMixin, View):
    """
    List pipeline definitions.

    GET /orchestration/definitions/
    """

    def get(self, request):
        """List all pipeline definitions."""
        active_only = request.GET.get("active", "false").lower() == "true"

        qs = PipelineDefinition.objects.all()
        if active_only:
            qs = qs.filter(is_active=True)

        definitions = [
            {
                "name": d.name,
                "description": d.description,
                "version": d.version,
                "is_active": d.is_active,
                "node_count": len(d.get_nodes()),
                "tags": d.tags,
                "updated_at": d.updated_at.isoformat(),
            }
            for d in qs
        ]

        return self.json_response({"definitions": definitions, "count": len(definitions)})


@method_decorator(csrf_exempt, name="dispatch")
class PipelineDefinitionDetailView(JSONResponseMixin, View):
    """
    Get, update, or delete a pipeline definition.

    GET /orchestration/definitions/<name>/
    PUT /orchestration/definitions/<name>/
    DELETE /orchestration/definitions/<name>/
    """

    def get(self, request, name):
        """Get a specific pipeline definition."""
        try:
            definition = PipelineDefinition.objects.get(name=name)
        except PipelineDefinition.DoesNotExist:
            return self.error_response(f"Definition not found: {name}", status=404)

        return self.json_response(
            {
                "name": definition.name,
                "description": definition.description,
                "version": definition.version,
                "is_active": definition.is_active,
                "config": definition.config,
                "tags": definition.tags,
                "created_by": definition.created_by,
                "created_at": definition.created_at.isoformat(),
                "updated_at": definition.updated_at.isoformat(),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class PipelineDefinitionValidateView(JSONResponseMixin, View):
    """
    Validate a pipeline definition.

    POST /orchestration/definitions/<name>/validate/
    """

    def post(self, request, name):
        """Validate a pipeline definition."""
        try:
            definition = PipelineDefinition.objects.get(name=name)
        except PipelineDefinition.DoesNotExist:
            return self.error_response(f"Definition not found: {name}", status=404)

        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()

        return self.json_response(
            {
                "name": definition.name,
                "valid": len(errors) == 0,
                "errors": errors,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class PipelineDefinitionExecuteView(JSONResponseMixin, View):
    """
    Execute a pipeline definition.

    POST /orchestration/definitions/<name>/execute/
    """

    def post(self, request, name):
        """Execute a specific pipeline definition."""
        try:
            definition = PipelineDefinition.objects.get(name=name, is_active=True)
        except PipelineDefinition.DoesNotExist:
            return self.error_response(
                f"Active definition not found: {name}", status=404
            )

        try:
            body = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return self.error_response("Invalid JSON body", status=400)

        payload = body.get("payload", {})
        source = body.get("source", "api")
        trace_id = body.get("trace_id")
        environment = body.get("environment", "production")
        incident_id = body.get("incident_id")

        try:
            orchestrator = DefinitionBasedOrchestrator(definition)
            result = orchestrator.execute(
                payload=payload,
                source=source,
                trace_id=trace_id,
                environment=environment,
                incident_id=incident_id,
            )
            return self.json_response(result)

        except Exception as e:
            logger.exception(f"Error executing definition {name}")
            return self.error_response(f"Execution failed: {str(e)}", status=500)
```

**Step 4: Update urls.py**

```python
# apps/orchestration/urls.py
"""URL configuration for the orchestration app."""

from django.urls import path

from apps.orchestration.views import (
    PipelineDefinitionDetailView,
    PipelineDefinitionExecuteView,
    PipelineDefinitionListView,
    PipelineDefinitionValidateView,
    PipelineWebhookView,
)

app_name = "orchestration"

urlpatterns = [
    # Legacy pipeline webhook
    path("pipeline/", PipelineWebhookView.as_view(), name="pipeline"),
    # Pipeline definition management
    path("definitions/", PipelineDefinitionListView.as_view(), name="definition-list"),
    path(
        "definitions/<str:name>/",
        PipelineDefinitionDetailView.as_view(),
        name="definition-detail",
    ),
    path(
        "definitions/<str:name>/validate/",
        PipelineDefinitionValidateView.as_view(),
        name="definition-validate",
    ),
    path(
        "definitions/<str:name>/execute/",
        PipelineDefinitionExecuteView.as_view(),
        name="definition-execute",
    ),
]
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_views.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/views.py apps/orchestration/urls.py apps/orchestration/_tests/
git commit -m "feat(orchestration): add API endpoints for pipeline definitions"
```

---

### Task 11: Create TransformNodeHandler for data transformation

**Files:**
- Create: `apps/orchestration/nodes/transform.py`
- Modify: `apps/orchestration/nodes/__init__.py`

**Step 1: Write the failing test**

Add to `apps/orchestration/_tests/test_nodes.py`:

```python
class TestTransformNodeHandler:
    """Tests for TransformNodeHandler."""

    def test_execute_with_jq_like_transform(self):
        """Test executing transform node with jq-like expression."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("transform")
        ctx = NodeContext(
            trace_id="test-trace",
            run_id="test-run",
            previous_outputs={
                "analyze": {
                    "recommendations": [
                        {"title": "High Memory", "priority": "high"},
                        {"title": "Low Disk", "priority": "medium"},
                    ],
                }
            },
        )
        config = {
            "source_node": "analyze",
            "extract": "recommendations",
            "filter_priority": "high",
        }

        result = handler.execute(ctx, config)

        assert result.node_type == "transform"
        assert not result.has_errors
        assert "transformed" in result.output

    def test_execute_with_mapping(self):
        """Test transform node with field mapping."""
        from apps.orchestration.nodes import NodeContext, get_node_handler

        handler = get_node_handler("transform")
        ctx = NodeContext(
            trace_id="t",
            run_id="r",
            previous_outputs={
                "context": {"system": {"cpu": {"percent": 85.5}}},
            },
        )
        config = {
            "source_node": "context",
            "mapping": {
                "cpu_usage": "system.cpu.percent",
            },
        }

        result = handler.execute(ctx, config)

        assert "transformed" in result.output
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestTransformNodeHandler -v`
Expected: FAIL

**Step 3: Write the transform handler**

```python
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
            source_node = config.get("source_node")
            source_data = ctx.previous_outputs.get(source_node, {})

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
            if mapping:
                mapped = {}
                for target_key, source_path in mapping.items():
                    value = self._get_nested(
                        ctx.previous_outputs.get(source_node, {}), source_path
                    )
                    mapped[target_key] = value
                result.output = {"transformed": mapped, "source_node": source_node}
            else:
                result.output = {"transformed": source_data, "source_node": source_node}

        except Exception as e:
            logger.exception(f"Error in TransformNodeHandler: {e}")
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
```

**Step 4: Register handler in __init__.py**

Update `apps/orchestration/nodes/__init__.py` to add:

```python
from apps.orchestration.nodes.transform import TransformNodeHandler

register_node_handler("transform", TransformNodeHandler)
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest apps/orchestration/_tests/test_nodes.py::TestTransformNodeHandler -v`
Expected: PASS

**Step 6: Commit**

```bash
git add apps/orchestration/nodes/
git commit -m "feat(orchestration): add TransformNodeHandler for data transformation"
```

---

### Task 12: Final integration test and verification

**Files:**
- Create: `apps/orchestration/_tests/test_integration.py`

**Step 1: Write integration test**

```python
# apps/orchestration/_tests/test_integration.py
"""Integration tests for the complete pipeline system."""

import pytest

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition, PipelineRun


@pytest.mark.django_db
class TestPipelineIntegration:
    """Integration tests for complete pipelines."""

    def test_context_to_intelligence_pipeline(self):
        """Test a pipeline that gathers context and runs intelligence."""
        definition = PipelineDefinition.objects.create(
            name="context-intelligence",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "gather",
                        "type": "context",
                        "config": {"include": ["cpu", "memory"]},
                        "next": "analyze",
                    },
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        assert result["status"] == "completed"
        assert "gather" in result["executed_nodes"]
        assert "analyze" in result["executed_nodes"]
        assert "system" in result["node_results"]["gather"]["output"]

    def test_pipeline_with_optional_failing_node(self):
        """Test that optional nodes don't break the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="optional-fail",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "context",
                        "type": "context",
                        "config": {"include": ["cpu"]},
                        "next": "bad_notify",
                    },
                    {
                        "id": "bad_notify",
                        "type": "notify",
                        "required": False,  # Optional - won't fail pipeline
                        "config": {
                            "driver": "generic",
                            "driver_config": {"endpoint": "http://invalid.invalid"},
                        },
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        # Pipeline should complete despite notify failure
        assert result["status"] in ("completed", "partial")
        assert "context" in result["executed_nodes"]

    def test_transform_between_nodes(self):
        """Test transform node processes data between stages."""
        definition = PipelineDefinition.objects.create(
            name="with-transform",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "context",
                        "type": "context",
                        "config": {"include": ["cpu", "memory"]},
                        "next": "transform",
                    },
                    {
                        "id": "transform",
                        "type": "transform",
                        "config": {
                            "source_node": "context",
                            "mapping": {
                                "cpu_pct": "system.cpu.percent",
                                "mem_pct": "system.memory.percent",
                            },
                        },
                        "next": "analyze",
                    },
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="test")

        assert result["status"] == "completed"
        transform_output = result["node_results"]["transform"]["output"]
        assert "transformed" in transform_output

    def test_pipeline_creates_run_record(self):
        """Test that pipeline execution creates proper records."""
        definition = PipelineDefinition.objects.create(
            name="record-test",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "analyze",
                        "type": "intelligence",
                        "config": {"provider": "local"},
                    },
                ],
            },
        )

        orchestrator = DefinitionBasedOrchestrator(definition)
        result = orchestrator.execute(payload={}, source="integration-test")

        # Verify PipelineRun was created
        run = PipelineRun.objects.get(run_id=result["run_id"])
        assert run.source == "integration-test"
        assert run.status in ("notified", "completed")

        # Verify stage executions were created
        stages = run.stage_executions.all()
        assert stages.count() >= 1
```

**Step 2: Run all tests**

Run: `uv run pytest apps/orchestration/_tests/ -v`
Expected: All tests PASS

**Step 3: Run Django check**

Run: `uv run python manage.py check`
Expected: `System check identified no issues`

**Step 4: Commit**

```bash
git add apps/orchestration/_tests/
git commit -m "test(orchestration): add integration tests for reusable pipelines"
```

---

## Verification Commands

After each task:
```bash
# Run Django check
uv run python manage.py check

# Run orchestration tests
uv run pytest apps/orchestration/_tests/ -v

# Run specific test file
uv run pytest apps/orchestration/_tests/test_definition_orchestrator.py -v
```

## Example Pipeline Configurations

### Alert-Triggered: Ingest → Analyze → Notify
```json
{
  "version": "1.0",
  "description": "Standard alert pipeline - receives webhook, analyzes, notifies",
  "nodes": [
    {"id": "ingest", "type": "ingest", "config": {"driver": "alertmanager"}, "next": "analyze"},
    {"id": "analyze", "type": "intelligence", "config": {"provider": "openai"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack", "channel": "#incidents"}}
  ]
}
```
**Trigger:** `POST /orchestration/pipeline/?definition=alert-pipeline`

### Alert-Triggered: Ingest → Local Analysis → PagerDuty
```json
{
  "version": "1.0",
  "description": "Critical alerts with local analysis and PagerDuty",
  "nodes": [
    {"id": "ingest", "type": "ingest", "next": "analyze"},
    {"id": "analyze", "type": "intelligence", "config": {"provider": "local"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "pagerduty"}}
  ]
}
```

### Standalone: Context → OpenAI → Slack (Health Check)
```json
{
  "version": "1.0",
  "description": "Scheduled health check - gathers system metrics, analyzes, alerts",
  "nodes": [
    {"id": "context", "type": "context", "config": {"include": ["cpu", "memory", "disk"]}, "next": "analyze"},
    {"id": "analyze", "type": "intelligence", "config": {"provider": "openai"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack", "channel": "#alerts"}}
  ]
}
```
**Trigger:** `POST /orchestration/definitions/health-check/execute/`

### Standalone: Context → Transform → Notify (Simple Metrics Alert)
```json
{
  "version": "1.0",
  "description": "Simple metrics pipeline without AI",
  "nodes": [
    {"id": "context", "type": "context", "config": {"include": ["cpu", "memory"]}, "next": "transform"},
    {"id": "transform", "type": "transform", "config": {"source_node": "context", "mapping": {"cpu": "system.cpu.percent", "mem": "system.memory.percent"}}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack"}}
  ]
}
```

### Multi-AI Chain: Context → OpenAI → Transform → Notify
```json
{
  "version": "1.0",
  "description": "Gather metrics, analyze with OpenAI, filter results, notify",
  "nodes": [
    {"id": "context", "type": "context", "config": {"include": ["cpu", "memory", "processes"]}, "next": "openai"},
    {"id": "openai", "type": "intelligence", "config": {"provider": "openai"}, "next": "transform"},
    {"id": "transform", "type": "transform", "config": {"source_node": "openai", "extract": "recommendations", "filter_priority": "high"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack"}}
  ]
}
```

---

## Risk Assessment

- **Low risk:** Creating new models and node handlers (Tasks 1-7) - additive changes
- **Medium risk:** Creating orchestrator (Task 8) - new execution path, well isolated
- **Low risk:** Admin and API (Tasks 9-10) - standard Django patterns
- **Low risk:** Transform and integration tests (Tasks 11-12) - verification only

## Future Enhancements (Not in this plan)

1. **Parallel node execution** - Run independent nodes concurrently
2. **Condition nodes** - Branch based on expressions
3. **Claude provider** - Add Claude API integration to intelligence providers
4. **Visual pipeline editor** - React-based UI for drag-and-drop pipeline design
5. **Pipeline versioning** - Full version history with rollback
6. **Scheduled pipelines** - Cron-like execution triggers
7. **Check node** - Wrap existing CheckExecutor for diagnostic checks