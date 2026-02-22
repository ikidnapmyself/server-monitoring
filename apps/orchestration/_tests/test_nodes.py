"""Tests for pipeline node types."""

from unittest.mock import MagicMock, patch

import pytest

from apps.checkers.checkers.base import CheckResult, CheckStatus
from apps.orchestration.nodes import NodeType, get_node_handler, list_node_types


class TestNodeRegistry:
    """Tests for node type registry."""

    def test_list_node_types(self):
        """Test listing available node types."""
        types = list_node_types()
        assert "ingest" in types
        assert "intelligence" in types
        assert "notify" in types
        assert "context" in types
        assert "transform" in types

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


# -- Helpers for fake checkers --


class _OkChecker:
    def check(self):
        return CheckResult(status=CheckStatus.OK, message="All good", metrics={"usage": 10})


class _WarningChecker:
    def check(self):
        return CheckResult(status=CheckStatus.WARNING, message="High usage", metrics={"usage": 80})


class _CriticalChecker:
    def check(self):
        return CheckResult(status=CheckStatus.CRITICAL, message="Critical!", metrics={"usage": 95})


class _BrokenChecker:
    def check(self):
        raise RuntimeError("Checker exploded")


_FAKE_REGISTRY = {
    "cpu": _OkChecker,
    "memory": _WarningChecker,
    "disk": _CriticalChecker,
}


class TestContextNodeHandler:
    """Tests for ContextNodeHandler."""

    @patch(
        "apps.checkers.checkers.get_enabled_checkers",
        return_value={"cpu": _OkChecker, "memory": _WarningChecker},
    )
    @patch("apps.checkers.checkers.CHECKER_REGISTRY", _FAKE_REGISTRY)
    def test_runs_enabled_checkers(self, _mock_enabled):
        """Runs all enabled checkers when no checker_names in config."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {})

        assert result.node_type == "context"
        assert result.output["checks_run"] == 2
        assert result.output["checks_passed"] == 1
        assert result.output["checks_failed"] == 1
        assert "cpu" in result.output["results"]
        assert "memory" in result.output["results"]
        assert result.output["results"]["cpu"]["status"] == "ok"
        assert result.output["results"]["memory"]["status"] == "warning"

    @patch("apps.checkers.checkers.CHECKER_REGISTRY", _FAKE_REGISTRY)
    def test_runs_specific_checkers(self):
        """Runs only checkers specified in config."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {"checker_names": ["disk"]})

        assert result.output["checks_run"] == 1
        assert result.output["results"]["disk"]["status"] == "critical"

    @patch("apps.checkers.checkers.CHECKER_REGISTRY", {"broken": _BrokenChecker})
    def test_handles_checker_exception(self):
        """Records error but doesn't fail the node when a checker raises."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {"checker_names": ["broken"]})

        assert result.output["checks_run"] == 1
        assert result.output["checks_failed"] == 1
        assert result.output["results"]["broken"]["status"] == "unknown"
        assert "exploded" in result.output["results"]["broken"]["message"]
        # Node itself should not have errors (individual checker failure is not a node error)
        assert not result.has_errors

    @patch("apps.checkers.checkers.CHECKER_REGISTRY", _FAKE_REGISTRY)
    def test_skips_unknown_checker_names(self):
        """Unknown checker names are skipped, valid ones still run."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {"checker_names": ["cpu", "nonexistent"]})

        assert result.output["checks_run"] == 1
        assert "nonexistent" not in result.output["results"]

    @patch("apps.checkers.checkers.CHECKER_REGISTRY", {})
    def test_errors_when_no_valid_checkers(self):
        """Node errors when no valid checkers can be resolved."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("context")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {"checker_names": ["nonexistent"]})

        assert result.has_errors
        assert "No valid checkers" in result.errors[0]

    def test_validate_config_accepts_empty(self):
        """Empty config is valid (runs all enabled checkers)."""
        handler = get_node_handler("context")
        errors = handler.validate_config({})
        assert errors == []


@pytest.mark.django_db
class TestNotifyNodeHandler:
    """Tests for NotifyNodeHandler."""

    def test_sends_to_matching_db_channels(self):
        """Sends notification to active channels matching configured drivers."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.nodes import NodeContext

        NotificationChannel.objects.create(
            name="test-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/test"},
            is_active=True,
        )

        handler = get_node_handler("notify")
        ctx = NodeContext(trace_id="t", run_id="r")

        mock_send = MagicMock(return_value={"success": True, "message_id": "msg-1"})
        with patch("apps.notify.views.DRIVER_REGISTRY") as mock_registry:
            mock_driver_cls = MagicMock()
            mock_driver_instance = MagicMock()
            mock_driver_instance.validate_config.return_value = True
            mock_driver_instance.send = mock_send
            mock_driver_cls.return_value = mock_driver_instance
            mock_registry.get.return_value = mock_driver_cls

            result = handler.execute(ctx, {"drivers": ["slack"]})

        assert result.output["channels_attempted"] == 1
        assert result.output["channels_succeeded"] == 1
        assert result.output["deliveries"][0]["status"] == "success"
        assert not result.has_errors

    def test_accepts_singular_driver_config(self):
        """Accepts 'driver' (string) in addition to 'drivers' (list)."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.nodes import NodeContext

        NotificationChannel.objects.create(
            name="test-generic",
            driver="generic",
            config={"endpoint_url": "https://example.com/hook"},
            is_active=True,
        )

        handler = get_node_handler("notify")
        ctx = NodeContext(trace_id="t", run_id="r")

        with patch("apps.notify.views.DRIVER_REGISTRY") as mock_registry:
            mock_driver_cls = MagicMock()
            mock_driver_instance = MagicMock()
            mock_driver_instance.validate_config.return_value = True
            mock_driver_instance.send.return_value = {"success": True}
            mock_driver_cls.return_value = mock_driver_instance
            mock_registry.get.return_value = mock_driver_cls

            result = handler.execute(ctx, {"driver": "generic"})

        assert result.output["channels_attempted"] == 1
        assert result.output["channels_succeeded"] == 1

    def test_errors_when_no_drivers_configured(self):
        """Returns error when neither drivers nor driver is in config."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("notify")
        ctx = NodeContext(trace_id="t", run_id="r")
        result = handler.execute(ctx, {})

        assert result.has_errors
        assert "Missing" in result.errors[0]

    def test_errors_when_no_active_channels(self):
        """Returns error when no active DB channels match configured drivers."""
        from apps.orchestration.nodes import NodeContext

        handler = get_node_handler("notify")
        ctx = NodeContext(trace_id="t", run_id="r")

        with patch("apps.notify.services.NotifySelector.resolve") as mock_resolve:
            mock_resolve.return_value = ("generic", {}, "generic", None, None, "default")
            result = handler.execute(ctx, {"drivers": ["slack"]})

        assert result.has_errors

    def test_handles_send_failure(self):
        """Records failure when driver.send() raises."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.nodes import NodeContext

        NotificationChannel.objects.create(
            name="bad-channel",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/bad"},
            is_active=True,
        )

        handler = get_node_handler("notify")
        ctx = NodeContext(trace_id="t", run_id="r")

        with patch("apps.notify.views.DRIVER_REGISTRY") as mock_registry:
            mock_driver_cls = MagicMock()
            mock_driver_instance = MagicMock()
            mock_driver_instance.validate_config.return_value = True
            mock_driver_instance.send.side_effect = ConnectionError("timeout")
            mock_driver_cls.return_value = mock_driver_instance
            mock_registry.get.return_value = mock_driver_cls

            result = handler.execute(ctx, {"drivers": ["slack"]})

        assert result.output["channels_attempted"] == 1
        assert result.output["channels_failed"] == 1
        assert result.output["deliveries"][0]["status"] == "failed"
        assert "timeout" in result.output["deliveries"][0]["error"]

    def test_builds_message_from_checker_results(self):
        """Message includes checker results from previous outputs."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.nodes import NodeContext

        NotificationChannel.objects.create(
            name="test-slack",
            driver="slack",
            config={"webhook_url": "https://hooks.slack.com/test"},
            is_active=True,
        )

        handler = get_node_handler("notify")
        ctx = NodeContext(
            trace_id="t",
            run_id="r",
            previous_outputs={
                "check_health": {
                    "checks_run": 2,
                    "checks_passed": 1,
                    "checks_failed": 1,
                    "results": {
                        "cpu": {"status": "ok", "message": "CPU fine"},
                        "memory": {"status": "warning", "message": "Memory high"},
                    },
                }
            },
        )

        captured_message = None

        def capture_send(message, config):
            nonlocal captured_message
            captured_message = message
            return {"success": True}

        with patch("apps.notify.views.DRIVER_REGISTRY") as mock_registry:
            mock_driver_cls = MagicMock()
            mock_driver_instance = MagicMock()
            mock_driver_instance.validate_config.return_value = True
            mock_driver_instance.send = capture_send
            mock_driver_cls.return_value = mock_driver_instance
            mock_registry.get.return_value = mock_driver_cls

            handler.execute(ctx, {"drivers": ["slack"]})

        assert captured_message is not None
        assert captured_message.severity == "warning"
        assert "Warning" in captured_message.title
        assert "memory" in captured_message.message.lower()

    def test_validate_config_requires_driver(self):
        """Validation fails without drivers or driver."""
        handler = get_node_handler("notify")
        errors = handler.validate_config({})
        assert len(errors) > 0

    def test_validate_config_accepts_drivers_list(self):
        """Validation passes with drivers list."""
        handler = get_node_handler("notify")
        errors = handler.validate_config({"drivers": ["slack"]})
        assert errors == []

    def test_validate_config_accepts_driver_string(self):
        """Validation passes with driver string."""
        handler = get_node_handler("notify")
        errors = handler.validate_config({"driver": "slack"})
        assert errors == []


@pytest.mark.django_db
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

    def test_validate_config_always_valid(self):
        """Test ingest node config validation (minimal requirements)."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("ingest")
        errors = handler.validate_config({})

        # Ingest has no required config
        assert len(errors) == 0


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

    def test_validate_missing_source_node(self):
        """Test validation fails without source_node."""
        from apps.orchestration.nodes import get_node_handler

        handler = get_node_handler("transform")
        errors = handler.validate_config({})

        assert len(errors) > 0
        assert any("source_node" in e for e in errors)
