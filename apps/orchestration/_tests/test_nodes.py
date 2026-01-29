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


class TestIntelligenceNodeHandler:
    """Tests for IntelligenceNodeHandler."""

    def test_execute_with_local_provider(self):
        """Test executing intelligence node with local provider."""
        from apps.orchestration.nodes import NodeContext

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
        handler = get_node_handler("intelligence")
        errors = handler.validate_config({})

        assert len(errors) > 0
        assert any("provider" in e.lower() for e in errors)

    def test_validate_config_valid(self):
        """Test validation passes with provider."""
        handler = get_node_handler("intelligence")
        errors = handler.validate_config({"provider": "local"})

        assert len(errors) == 0


class TestNotifyNodeHandler:
    """Tests for NotifyNodeHandler."""

    def test_execute_with_driver(self):
        handler = get_node_handler("notify")
        from apps.orchestration.nodes import NodeContext

        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {"driver": "generic"})

        assert result.node_type == "notify"
        assert result.output.get("delivered") is True

    def test_validate_missing_driver(self):
        handler = get_node_handler("notify")
        errors = handler.validate_config({})
        assert len(errors) > 0


class TestContextNodeHandler:
    """Tests for ContextNodeHandler."""

    def test_execute_default(self):
        handler = get_node_handler("context")
        from apps.orchestration.nodes import NodeContext

        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {})

        assert result.node_type == "context"
        assert "context" in result.output
