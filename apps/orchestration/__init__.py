"""
Pipeline Orchestration app.

This app controls the full lifecycle of an incident through a strict, linear chain:
alerts → checkers → intelligence → notify

Key concepts:
- Single orchestrator with correlation ID (trace_id/run_id)
- State machine: PENDING → INGESTED → CHECKED → ANALYZED → NOTIFIED (or FAILED/SKIPPED)
- Structured DTOs between stages (each stage is idempotent)
- Monitoring signals at every stage boundary
"""

default_app_config = "apps.orchestration.apps.OrchestrationConfig"
