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
        duration_ms = 0.0

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
