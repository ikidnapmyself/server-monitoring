# apps/orchestration/_tests/test_definition_orchestrator.py
"""Tests for DefinitionBasedOrchestrator."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.orchestration.definition_orchestrator import DefinitionBasedOrchestrator
from apps.orchestration.models import PipelineDefinition


def _patch_notify_drivers():
    """Patch DRIVER_REGISTRY so drivers don't make real HTTP calls."""
    mock_driver_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.validate_config.return_value = True
    mock_instance.send.return_value = {"success": True, "message_id": "mock-msg-1"}
    mock_driver_cls.return_value = mock_instance
    return patch("apps.notify.views.DRIVER_REGISTRY", {"generic": mock_driver_cls})


def _patch_intelligence_provider():
    """Patch get_provider so intelligence nodes don't do real system scanning."""
    mock_provider = MagicMock()
    mock_provider.run.return_value = [
        {"title": "mock-rec", "description": "mock recommendation", "priority": "low"}
    ]
    return patch("apps.intelligence.providers.get_provider", return_value=mock_provider)


def _simple_pipeline_config():
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


class TestDefinitionBasedOrchestrator(TestCase):
    """Tests for DefinitionBasedOrchestrator."""

    def test_execute_simple_pipeline(self):
        """Test executing a simple pipeline."""
        from apps.notify.models import NotificationChannel

        simple_pipeline_config = _simple_pipeline_config()

        NotificationChannel.objects.create(
            name="test-generic",
            driver="generic",
            config={"endpoint_url": "https://example.com/hook"},
            is_active=True,
        )

        definition = PipelineDefinition.objects.create(
            name="test-simple",
            config=simple_pipeline_config,
        )

        with _patch_notify_drivers(), _patch_intelligence_provider():
            orchestrator = DefinitionBasedOrchestrator(definition)
            result = orchestrator.execute(
                payload={"test": "data"},
                source="test",
            )

        assert result["status"] in ("completed", "partial")
        assert "executed_nodes" in result
        assert len(result["executed_nodes"]) > 0

    def test_execute_records_pipeline_run(self):
        """Test that execution creates a PipelineRun record."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineRun

        simple_pipeline_config = _simple_pipeline_config()

        NotificationChannel.objects.create(
            name="test-generic",
            driver="generic",
            config={"endpoint_url": "https://example.com/hook"},
            is_active=True,
        )

        definition = PipelineDefinition.objects.create(
            name="test-record",
            config=simple_pipeline_config,
        )

        with _patch_notify_drivers(), _patch_intelligence_provider():
            orchestrator = DefinitionBasedOrchestrator(definition)
            result = orchestrator.execute(
                payload={"test": "data"},
                source="test",
            )

        # Check PipelineRun was created
        run = PipelineRun.objects.filter(run_id=result["run_id"]).first()
        assert run is not None
        assert run.source == "test"

    def test_execute_chains_node_outputs(self):
        """Test that node outputs are passed to subsequent nodes."""
        from apps.notify.models import NotificationChannel

        simple_pipeline_config = _simple_pipeline_config()

        NotificationChannel.objects.create(
            name="test-generic",
            driver="generic",
            config={"endpoint_url": "https://example.com/hook"},
            is_active=True,
        )

        definition = PipelineDefinition.objects.create(
            name="test-chain",
            config=simple_pipeline_config,
        )

        with _patch_notify_drivers(), _patch_intelligence_provider():
            orchestrator = DefinitionBasedOrchestrator(definition)
            result = orchestrator.execute(
                payload={"test": "data"},
                source="test",
            )

        # Check that node results include outputs
        assert "node_results" in result
        for node_id, node_result in result["node_results"].items():
            assert "output" in node_result

    def test_validate_definition(self):
        """Test validating a pipeline definition."""
        simple_pipeline_config = _simple_pipeline_config()

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


class TestValidateEdgeCases(TestCase):
    """Tests for validate() edge cases."""

    def test_validate_no_nodes(self):
        """Pipeline with no nodes should produce a validation error."""
        definition = PipelineDefinition(name="empty", config={"version": "1.0", "nodes": []})
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("no nodes" in e.lower() for e in errors)

    def test_validate_missing_node_id(self):
        """Node missing 'id' should produce a validation error."""
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [{"type": "context", "config": {}}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("missing 'id'" in e.lower() for e in errors)

    def test_validate_duplicate_node_id(self):
        """Duplicate node IDs should produce a validation error."""
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "dup", "type": "context", "config": {}},
                    {"id": "dup", "type": "notify", "config": {}},
                ],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("duplicate" in e.lower() for e in errors)

    def test_validate_missing_node_type(self):
        """Node missing 'type' should produce a validation error."""
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [{"id": "n1"}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("missing 'type'" in e.lower() for e in errors)

    def test_validate_handler_raises_exception(self):
        """Handler validate_config raising should produce a validation error."""
        mock_handler = MagicMock()
        mock_handler.validate_config.side_effect = RuntimeError("boom")
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [{"id": "n1", "type": "context", "config": {}}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            errors = orchestrator.validate()
        assert any("validation error" in e.lower() for e in errors)

    def test_validate_unknown_next_node(self):
        """Reference to a non-existent next node should produce a validation error."""
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [{"id": "n1", "type": "context", "config": {}, "next": "nonexistent"}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("unknown next node" in e.lower() for e in errors)

    def test_validate_missing_version(self):
        """Config missing 'version' should produce a validation error."""
        definition = PipelineDefinition(
            name="no-ver",
            config={"nodes": [{"id": "n1", "type": "context", "config": {}}]},
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        errors = orchestrator.validate()
        assert any("version" in e.lower() for e in errors)

    def test_validate_handler_returns_errors(self):
        """Handler validate_config returning errors should be collected."""
        mock_handler = MagicMock()
        mock_handler.validate_config.return_value = ["bad config value"]
        definition = PipelineDefinition(
            name="t",
            config={
                "version": "1.0",
                "nodes": [{"id": "n1", "type": "context", "config": {}}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            errors = orchestrator.validate()
        assert any("bad config value" in e for e in errors)


class TestExecuteEdgeCases(TestCase):
    """Tests for execute() edge cases."""

    def _make_mock_handler(self, has_errors=False, output=None, errors=None):
        """Create a mock handler that returns a configured NodeResult."""
        mock_handler = MagicMock()
        mock_result = MagicMock()
        mock_result.has_errors = has_errors
        mock_result.output = output or {}
        mock_result.errors = errors or []
        mock_result.to_dict.return_value = {
            "node_id": "mock",
            "node_type": "mock",
            "output": mock_result.output,
            "errors": mock_result.errors,
        }
        mock_handler.execute.return_value = mock_result
        return mock_handler, mock_result

    def test_execute_with_explicit_trace_id(self):
        """Passing a trace_id should use it instead of generating one."""
        definition = PipelineDefinition.objects.create(
            name="trace-test",
            config={
                "version": "1.0",
                "nodes": [{"id": "ctx", "type": "context", "config": {}}],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test", trace_id="my-trace-123")
        assert result["trace_id"] == "my-trace-123"

    def test_execute_skips_node_on_skip_if_errors(self):
        """Node with skip_if_errors should be skipped when prereq has errors."""
        definition = PipelineDefinition.objects.create(
            name="skip-test",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}, "required": False},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_errors": ["ctx"],
                    },
                ],
            },
        )
        mock_handler = MagicMock()
        error_result = MagicMock()
        error_result.has_errors = True
        error_result.errors = ["check failed"]
        error_result.output = {}
        error_result.to_dict.return_value = {
            "node_id": "ctx",
            "node_type": "context",
            "errors": ["check failed"],
            "output": {},
        }
        mock_handler.execute.return_value = error_result
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["skipped_nodes"]

    def test_execute_skips_node_on_skip_if_condition(self):
        """Node with skip_if_condition should be skipped when condition is met."""
        definition = PipelineDefinition.objects.create(
            name="cond-skip",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}, "required": False},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_condition": "ctx.has_errors",
                    },
                ],
            },
        )
        mock_handler = MagicMock()
        error_result = MagicMock()
        error_result.has_errors = True
        error_result.errors = ["failed"]
        error_result.output = {}
        error_result.to_dict.return_value = {
            "node_id": "ctx",
            "node_type": "context",
            "errors": ["failed"],
            "output": {},
        }
        mock_handler.execute.return_value = error_result
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["skipped_nodes"]

    def test_execute_skip_if_condition_no_errors(self):
        """skip_if_condition should not skip when the referenced node has no errors."""
        definition = PipelineDefinition.objects.create(
            name="no-skip",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_condition": "ctx.has_errors",
                    },
                ],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["executed_nodes"]
        assert "notify" not in result["skipped_nodes"]

    def test_execute_skip_if_condition_unknown_node_ref(self):
        """skip_if_condition referencing unknown node should not skip."""
        definition = PipelineDefinition.objects.create(
            name="unknown-ref",
            config={
                "version": "1.0",
                "nodes": [
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_condition": "missing_node.has_errors",
                    },
                ],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["executed_nodes"]

    def test_execute_ingest_propagates_incident_id(self):
        """Ingest node that creates an incident should propagate incident_id."""
        from apps.alerts.models import Incident

        incident = Incident.objects.create(title="test incident")

        definition = PipelineDefinition.objects.create(
            name="ingest-test",
            config={
                "version": "1.0",
                "nodes": [{"id": "ingest", "type": "ingest", "config": {}}],
            },
        )
        mock_handler = MagicMock()
        mock_result = MagicMock()
        mock_result.has_errors = False
        mock_result.output = {"incident_id": incident.id}
        mock_result.to_dict.return_value = {
            "node_id": "ingest",
            "node_type": "ingest",
            "output": {"incident_id": incident.id},
            "errors": [],
        }
        mock_handler.execute.return_value = mock_result
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert result["incident_id"] == incident.id

    def test_execute_node_exception_fails_pipeline(self):
        """Required node raising an exception should fail the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="exc-test",
            config={
                "version": "1.0",
                "nodes": [{"id": "ctx", "type": "context", "config": {}}],
            },
        )
        mock_handler = MagicMock()
        mock_handler.execute.side_effect = RuntimeError("handler crashed")
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert result["status"] == "failed"
        assert "handler crashed" in result["error"]

    def test_execute_optional_node_exception_continues(self):
        """Non-required node raising should not stop the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="opt-exc",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}, "required": False},
                    {"id": "notify", "type": "notify", "config": {}},
                ],
            },
        )
        mock_handler = MagicMock()
        ok_result = MagicMock()
        ok_result.has_errors = False
        ok_result.output = {}
        ok_result.to_dict.return_value = {
            "node_id": "notify",
            "node_type": "notify",
            "output": {},
            "errors": [],
        }
        mock_handler.execute.side_effect = [RuntimeError("optional fail"), ok_result]
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert result["status"] == "completed"
        assert "notify" in result["executed_nodes"]

    def test_execute_outer_exception_handler(self):
        """Exception before node loop should be caught by outer handler."""
        from apps.orchestration.models import PipelineRun

        definition = PipelineDefinition.objects.create(
            name="outer-exc",
            config={
                "version": "1.0",
                "nodes": [{"id": "ctx", "type": "context", "config": {}}],
            },
        )
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch.object(PipelineRun, "mark_started", side_effect=RuntimeError("DB down")):
            result = orchestrator.execute(payload={}, source="test")
        assert result["status"] == "failed"
        assert "DB down" in result["error"]

    def test_execute_skip_if_errors_prereq_no_errors(self):
        """skip_if_errors should not skip when prereq node had no errors."""
        definition = PipelineDefinition.objects.create(
            name="no-skip-err",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_errors": ["ctx"],
                    },
                ],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["executed_nodes"]
        assert "notify" not in result["skipped_nodes"]

    def test_execute_skip_if_condition_unsupported_pattern(self):
        """skip_if_condition with unsupported pattern should not skip."""
        definition = PipelineDefinition.objects.create(
            name="unsup-cond",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_condition": "some_unsupported_expression",
                    },
                ],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert "notify" in result["executed_nodes"]

    def test_execute_skip_if_condition_exception_handled(self):
        """Exception in skip_if_condition evaluation should be silently caught."""
        definition = PipelineDefinition.objects.create(
            name="exc-cond",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {},
                        "skip_if_condition": "ctx.has_errors",
                    },
                ],
            },
        )
        mock_handler, _ = self._make_mock_handler()
        orchestrator = DefinitionBasedOrchestrator(definition)
        # Patch previous_results.get to raise inside _should_skip
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            # Manipulate node_results to make .get("errors") raise
            original_execute = orchestrator.execute

            def patched_execute(*args, **kwargs):
                return original_execute(*args, **kwargs)

            # Instead, directly test _should_skip with a bad previous_results dict
            bad_result = MagicMock()
            bad_result.get.side_effect = RuntimeError("kaboom")
            should_skip = orchestrator._should_skip(
                {"id": "notify", "skip_if_condition": "ctx.has_errors"},
                {"ctx": bad_result},
            )
        assert should_skip is False

    def test_execute_required_node_with_errors_fails_pipeline(self):
        """Required node returning errors should fail the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="req-fail",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}},
                    {"id": "notify", "type": "notify", "config": {}},
                ],
            },
        )
        mock_handler = MagicMock()
        error_result = MagicMock()
        error_result.has_errors = True
        error_result.errors = ["critical failure"]
        error_result.output = {}
        error_result.to_dict.return_value = {
            "node_id": "ctx",
            "node_type": "context",
            "errors": ["critical failure"],
            "output": {},
        }
        mock_handler.execute.return_value = error_result
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert result["status"] == "failed"
        assert "notify" not in result["executed_nodes"]

    def test_execute_non_required_node_errors_continues(self):
        """Non-required node with errors should not stop the pipeline."""
        definition = PipelineDefinition.objects.create(
            name="opt-err",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "ctx", "type": "context", "config": {}, "required": False},
                    {"id": "notify", "type": "notify", "config": {}},
                ],
            },
        )
        mock_handler = MagicMock()
        error_result = MagicMock()
        error_result.has_errors = True
        error_result.errors = ["non-critical"]
        error_result.output = {}
        error_result.to_dict.return_value = {
            "node_id": "ctx",
            "node_type": "context",
            "errors": ["non-critical"],
            "output": {},
        }
        ok_result = MagicMock()
        ok_result.has_errors = False
        ok_result.output = {"done": True}
        ok_result.to_dict.return_value = {
            "node_id": "notify",
            "node_type": "notify",
            "output": {"done": True},
            "errors": [],
        }
        mock_handler.execute.side_effect = [error_result, ok_result]
        orchestrator = DefinitionBasedOrchestrator(definition)
        with patch(
            "apps.orchestration.definition_orchestrator.get_node_handler",
            return_value=mock_handler,
        ):
            result = orchestrator.execute(payload={}, source="test")
        assert result["status"] == "completed"
        assert "ctx" in result["executed_nodes"]
        assert "notify" in result["executed_nodes"]
