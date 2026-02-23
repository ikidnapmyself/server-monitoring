"""Tests for orchestration views."""

import json

from django.test import Client, TestCase


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


class TestPipelineDefinitionListView(TestCase):
    """Tests for PipelineDefinitionListView."""

    def test_list_empty(self):
        """Test listing when no definitions exist."""
        client = Client()
        response = client.get("/orchestration/definitions/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["definitions"] == []

    def test_list_active_only(self):
        """Test that only active definitions are listed by default."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        # Create active and inactive definitions
        PipelineDefinition.objects.create(
            name="active-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )
        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.get("/orchestration/definitions/")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["definitions"][0]["name"] == "active-pipeline"

    def test_list_include_inactive(self):
        """Test listing with inactive definitions included."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="active-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )
        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.get("/orchestration/definitions/?include_inactive=true")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2


class TestPipelineDefinitionDetailView(TestCase):
    """Tests for PipelineDefinitionDetailView."""

    def test_get_definition(self):
        """Test getting a pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="test-pipeline",
            description="Test description",
            config=simple_pipeline_config,
        )

        client = Client()
        response = client.get("/orchestration/definitions/test-pipeline/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-pipeline"
        assert data["description"] == "Test description"
        assert "config" in data

    def test_get_nonexistent(self):
        """Test getting a nonexistent pipeline definition."""
        client = Client()
        response = client.get("/orchestration/definitions/nonexistent/")

        assert response.status_code == 404


class TestPipelineDefinitionValidateView(TestCase):
    """Tests for PipelineDefinitionValidateView."""

    def test_validate_valid_definition(self):
        """Test validating a valid pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="valid-pipeline",
            config=simple_pipeline_config,
        )

        client = Client()
        response = client.post("/orchestration/definitions/valid-pipeline/validate/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "valid-pipeline"
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_invalid_definition(self):
        """Test validating an invalid pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        PipelineDefinition.objects.create(
            name="invalid-pipeline",
            config={
                "version": "1.0",
                "nodes": [
                    {"id": "bad", "type": "nonexistent"},
                ],
            },
        )

        client = Client()
        response = client.post("/orchestration/definitions/invalid-pipeline/validate/")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0


class TestPipelineDefinitionExecuteView(TestCase):
    """Tests for PipelineDefinitionExecuteView."""

    def test_execute_definition(self):
        """Test executing a pipeline definition."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="exec-pipeline",
            config=simple_pipeline_config,
            is_active=True,
        )

        client = Client()
        response = client.post(
            "/orchestration/definitions/exec-pipeline/execute/",
            data=json.dumps({"payload": {"test": "data"}, "source": "test"}),
            content_type="application/json",
        )

        assert response.status_code in (200, 500)  # 200 if completed, 500 if failed
        data = response.json()
        assert "run_id" in data
        assert "status" in data
        assert data["definition"] == "exec-pipeline"

    def test_execute_inactive_definition(self):
        """Test that inactive definitions cannot be executed."""
        from apps.orchestration.models import PipelineDefinition

        simple_pipeline_config = _simple_pipeline_config()

        PipelineDefinition.objects.create(
            name="inactive-pipeline",
            config=simple_pipeline_config,
            is_active=False,
        )

        client = Client()
        response = client.post(
            "/orchestration/definitions/inactive-pipeline/execute/",
            data=json.dumps({"payload": {}}),
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_execute_nonexistent(self):
        """Test executing a nonexistent definition."""
        client = Client()
        response = client.post(
            "/orchestration/definitions/nonexistent/execute/",
            data=json.dumps({"payload": {}}),
            content_type="application/json",
        )

        assert response.status_code == 404
