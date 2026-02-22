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
                "config": {"checker_names": ["cpu", "memory", "disk"]},
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
