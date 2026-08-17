"""Shared test fixtures for orchestration app."""

import logging

import pytest

from apps.orchestration.models import PipelineDefinition


@pytest.fixture
def apps_logging_propagates():
    """Let ``caplog`` see records emitted under the ``apps.*`` logger.

    ``config.settings.LOGGING`` gives the ``apps`` logger ``propagate: False``, so its
    records never reach the root handler pytest installs and ``caplog.text`` comes back
    empty while the code under test is logging correctly. Restoring propagation for the
    duration of a test makes plain ``caplog.at_level`` work, and keeps ``caplog.records``
    available so assertions can pin the logged *facts* via ``record.args`` rather than
    the wording of the sentence.
    """
    logger = logging.getLogger("apps")
    previous = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = previous


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
