# apps/orchestration/_tests/test_pipeline_definition.py
"""Tests for PipelineDefinition model."""

import pytest
from django.test import TestCase

from apps.orchestration.models import PipelineDefinition


class TestPipelineDefinition(TestCase):
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
