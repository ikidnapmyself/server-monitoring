"""Tests for DTO serialization."""

from django.test import TestCase

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    PipelineResult,
    StageContext,
    StageError,
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

    def test_stage_error_to_dict(self):
        """Test StageError serialization."""
        error = StageError(error_type="ValueError", message="bad input")
        data = error.to_dict()
        assert data["error_type"] == "ValueError"
        assert data["message"] == "bad input"
        assert data["retryable"] is True

    def test_pipeline_result_to_dict_no_stages(self):
        """Test PipelineResult with no stage results (all None)."""
        result = PipelineResult(trace_id="t", run_id="r", status="FAILED")
        data = result.to_dict()
        assert "ingest" not in data
        assert "check" not in data
        assert "analyze" not in data
        assert "notify" not in data
        assert "final_error" not in data

    def test_pipeline_result_to_dict_all_stages(self):
        """Test PipelineResult with all stages and final_error."""
        result = PipelineResult(
            trace_id="t",
            run_id="r",
            status="COMPLETED",
            ingest=IngestResult(),
            check=CheckResult(),
            analyze=AnalyzeResult(),
            notify=NotifyResult(),
            final_error=StageError(error_type="Err", message="msg"),
        )
        data = result.to_dict()
        assert "ingest" in data
        assert "check" in data
        assert "analyze" in data
        assert "notify" in data
        assert "final_error" in data
        assert data["final_error"]["error_type"] == "Err"

    def test_check_result_has_errors(self):
        assert CheckResult(errors=["e"]).has_errors is True
        assert CheckResult().has_errors is False

    def test_analyze_result_has_errors(self):
        assert AnalyzeResult(errors=["e"]).has_errors is True
        assert AnalyzeResult().has_errors is False

    def test_notify_result_has_errors(self):
        assert NotifyResult(errors=["e"]).has_errors is True
        assert NotifyResult().has_errors is False
