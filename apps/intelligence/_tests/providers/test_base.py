"""Tests for BaseProvider.run() and _redact_config()."""

from unittest.mock import patch

from django.test import TestCase

from apps.intelligence.models import AnalysisRun, AnalysisRunStatus
from apps.intelligence.providers.base import (
    BaseProvider,
    Recommendation,
    RecommendationPriority,
    RecommendationType,
)


class FakeProvider(BaseProvider):
    """Concrete provider for testing."""

    name = "fake"
    description = "Fake provider for tests"

    def __init__(self, recommendations=None, error=None):
        self._recommendations = recommendations or [
            Recommendation(
                type=RecommendationType.GENERAL,
                priority=RecommendationPriority.MEDIUM,
                title="Test recommendation",
                description="Test description",
                actions=["Do something"],
            )
        ]
        self._error = error

    def analyze(self, incident=None, analysis_type=""):
        if self._error:
            raise self._error
        return self._recommendations


class BaseProviderRunTests(TestCase):
    """Tests for BaseProvider.run() audit logging."""

    def test_run_returns_recommendations(self):
        """run() returns the same list as analyze()."""
        expected = [
            Recommendation(
                type=RecommendationType.CPU,
                priority=RecommendationPriority.HIGH,
                title="High CPU",
                description="CPU is high",
            )
        ]
        provider = FakeProvider(recommendations=expected)
        result = provider.run()
        self.assertEqual(result, expected)

    def test_run_creates_analysis_run_record(self):
        """run() creates an AnalysisRun DB row with correct fields."""
        provider = FakeProvider()
        provider.run()

        self.assertEqual(AnalysisRun.objects.count(), 1)
        row = AnalysisRun.objects.first()
        self.assertEqual(row.provider, "fake")
        self.assertEqual(row.status, AnalysisRunStatus.SUCCEEDED)
        self.assertEqual(row.recommendations_count, 1)

    def test_run_records_succeeded_status(self):
        """Succeeded run has started_at, completed_at, and duration_ms set."""
        provider = FakeProvider()
        provider.run()

        row = AnalysisRun.objects.first()
        self.assertIsNotNone(row.started_at)
        self.assertIsNotNone(row.completed_at)
        self.assertGreaterEqual(row.duration_ms, 0)

    def test_run_records_failed_status_and_reraises(self):
        """analyze() raises -> run() marks FAILED and re-raises."""
        provider = FakeProvider(error=RuntimeError("boom"))

        with self.assertRaises(RuntimeError, msg="boom"):
            provider.run()

        row = AnalysisRun.objects.first()
        self.assertEqual(row.status, AnalysisRunStatus.FAILED)
        self.assertIn("boom", row.error_message)

    def test_run_accepts_trace_id(self):
        """run(trace_id='abc') -> AnalysisRun.trace_id == 'abc'."""
        provider = FakeProvider()
        provider.run(trace_id="abc")

        row = AnalysisRun.objects.first()
        self.assertEqual(row.trace_id, "abc")

    def test_run_accepts_pipeline_run_id(self):
        """run(pipeline_run_id='run-123') -> AnalysisRun.pipeline_run_id == 'run-123'."""
        provider = FakeProvider()
        provider.run(pipeline_run_id="run-123")

        row = AnalysisRun.objects.first()
        self.assertEqual(row.pipeline_run_id, "run-123")

    def test_run_default_ids_empty(self):
        """No args -> trace_id == '', pipeline_run_id == ''."""
        provider = FakeProvider()
        provider.run()

        row = AnalysisRun.objects.first()
        self.assertEqual(row.trace_id, "")
        self.assertEqual(row.pipeline_run_id, "")

    def test_run_stores_incident_fk(self):
        """Pass a real Incident -> AnalysisRun.incident FK set."""
        from apps.alerts.models import AlertSeverity, Incident, IncidentStatus

        incident = Incident.objects.create(
            title="Test Incident",
            status=IncidentStatus.OPEN,
            severity=AlertSeverity.WARNING,
        )
        provider = FakeProvider()
        provider.run(incident=incident)

        row = AnalysisRun.objects.first()
        self.assertEqual(row.incident_id, incident.pk)

    def test_run_incident_none(self):
        """No incident -> AnalysisRun.incident is None."""
        provider = FakeProvider()
        provider.run()

        row = AnalysisRun.objects.first()
        self.assertIsNone(row.incident)

    def test_run_stores_recommendations_as_dicts(self):
        """Recommendations field contains list of dicts via to_dict()."""
        rec = Recommendation(
            type=RecommendationType.MEMORY,
            priority=RecommendationPriority.HIGH,
            title="Memory issue",
            description="Too much memory",
            actions=["Free memory"],
        )
        provider = FakeProvider(recommendations=[rec])
        provider.run()

        row = AnalysisRun.objects.first()
        self.assertEqual(len(row.recommendations), 1)
        self.assertIsInstance(row.recommendations[0], dict)
        self.assertEqual(row.recommendations[0]["type"], "memory")
        self.assertEqual(row.recommendations[0]["priority"], "high")
        self.assertEqual(row.recommendations[0]["title"], "Memory issue")

    def test_run_passes_incident_to_analyze(self):
        """Verify analyze is called with the incident."""
        provider = FakeProvider()
        with patch.object(provider, "analyze", wraps=provider.analyze) as mock_analyze:
            provider.run(incident="fake_incident")
            mock_analyze.assert_called_once_with("fake_incident", "")

    def test_run_passes_analysis_type_to_analyze(self):
        """Verify run(analysis_type='memory') passes it to analyze()."""
        provider = FakeProvider()
        with patch.object(provider, "analyze", wraps=provider.analyze) as mock_analyze:
            provider.run(analysis_type="memory")
            mock_analyze.assert_called_once_with(None, "memory")

    def test_run_returns_recommendations_when_db_fails(self):
        """DB failure -> recommendations still returned."""
        provider = FakeProvider()
        with patch("apps.intelligence.models.AnalysisRun.objects") as mock_objects:
            mock_objects.create.side_effect = RuntimeError("DB down")
            result = provider.run()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Test recommendation")

    def test_run_ignores_non_model_incident(self):
        """Non-model object as incident -> AnalysisRun.incident is None."""
        provider = FakeProvider()
        provider.run(incident="not-a-model-object")

        row = AnalysisRun.objects.first()
        self.assertIsNone(row.incident)

    def test_run_stores_redacted_config(self):
        """api_key='***' but model='gpt-4' stored in provider_config."""
        provider = FakeProvider()
        provider.run(
            provider_config={"api_key": "sk-secret-123", "model": "gpt-4"},
        )

        row = AnalysisRun.objects.first()
        self.assertEqual(row.provider_config["api_key"], "***")
        self.assertEqual(row.provider_config["model"], "gpt-4")


class RedactConfigTests(TestCase):
    """Tests for BaseProvider._redact_config()."""

    def test_redacts_key_patterns(self):
        """api_key, secret, token, password all -> '***', model stays."""
        config = {
            "api_key": "sk-123",
            "secret": "my-secret",
            "token": "tok-abc",
            "password": "hunter2",
            "model": "gpt-4",
        }
        result = BaseProvider._redact_config(config)
        self.assertEqual(result["api_key"], "***")
        self.assertEqual(result["secret"], "***")
        self.assertEqual(result["token"], "***")
        self.assertEqual(result["password"], "***")
        self.assertEqual(result["model"], "gpt-4")

    def test_empty_config(self):
        """{} -> {}."""
        result = BaseProvider._redact_config({})
        self.assertEqual(result, {})
