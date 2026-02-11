"""Tests for signal tags."""

from django.test import TestCase

from apps.orchestration.signals import SignalTags


class SignalTagsTests(TestCase):
    """Test signal tags."""

    def test_signal_tags_to_dict(self):
        """Test SignalTags serialization."""
        tags = SignalTags(
            trace_id="trace-123",
            run_id="run-456",
            stage="ingest",
            incident_id=1,
            source="grafana",
            alert_fingerprint="abc",
            environment="production",
            attempt=2,
            extra={"custom": "value"},
        )
        data = tags.to_dict()
        assert data["trace_id"] == "trace-123"
        assert data["stage"] == "ingest"
        assert data["custom"] == "value"
