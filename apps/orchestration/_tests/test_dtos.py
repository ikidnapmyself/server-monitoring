"""Tests for DTO serialization."""

from django.test import TestCase

from apps.orchestration.dtos import (
    IngestResult,
    PipelineResult,
    StageContext,
)


class DTOSerializationTests(TestCase):
    """Test DTO serialization."""

    def test_stage_context_to_dict(self):
        """Test StageContext serialization."""
        ctx = StageContext(
            trace_id="trace-123",
            run_id="run-456",
            incident_id=1,
            attempt=2,
            environment="staging",
            source="grafana",
        )
        data = ctx.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["run_id"] == "run-456"
        assert data["incident_id"] == 1
        assert data["attempt"] == 2

    def test_ingest_result_to_dict(self):
        """Test IngestResult serialization."""
        result = IngestResult(
            incident_id=1,
            alert_fingerprint="abc123",
            severity="critical",
            source="alertmanager",
            alerts_created=2,
        )
        data = result.to_dict()
        assert data["incident_id"] == 1
        assert data["alerts_created"] == 2
        assert result.has_errors is False

    def test_ingest_result_has_errors(self):
        """Test IngestResult error detection."""
        result = IngestResult(errors=["Error 1", "Error 2"])
        assert result.has_errors is True

    def test_pipeline_result_to_dict(self):
        """Test PipelineResult serialization."""
        result = PipelineResult(
            trace_id="trace-123",
            run_id="run-456",
            status="COMPLETED",
            incident_id=1,
            ingest=IngestResult(incident_id=1),
            stages_completed=["ingest", "check"],
        )
        data = result.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["status"] == "COMPLETED"
        assert "ingest" in data
        assert data["stages_completed"] == ["ingest", "check"]
