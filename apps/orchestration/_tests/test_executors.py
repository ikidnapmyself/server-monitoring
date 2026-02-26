"""Tests for AnalyzeExecutor (apps/orchestration/executors.py).

Covers the lines added/changed in the DB-driven provider selection commit:
- explicit provider_name → get_provider()
- absent provider_name  → get_active_provider()
- model_info uses provider.name with graceful fallback
- error path with fallback_enabled / fallback_disabled
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from apps.orchestration.dtos import AnalyzeResult, StageContext
from apps.orchestration.executors import AnalyzeExecutor


def _ctx(payload=None, incident_id=None):
    """Build a minimal StageContext for AnalyzeExecutor tests."""
    return StageContext(
        trace_id="trace-abc",
        run_id="run-xyz",
        incident_id=incident_id,
        payload=payload or {},
    )


class TestAnalyzeExecutorExplicitProvider(SimpleTestCase):
    """When payload contains 'provider', get_provider() is used."""

    def test_explicit_provider_calls_get_provider(self):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_provider", return_value=mock_provider
        ) as mock_gp:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider": "local"}))

        mock_gp.assert_called_once_with("local")
        assert isinstance(result, AnalyzeResult)
        assert not result.errors

    def test_explicit_provider_with_config_passes_kwargs(self):
        mock_provider = MagicMock()
        mock_provider.name = "openai"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_provider", return_value=mock_provider
        ) as mock_gp:
            executor = AnalyzeExecutor()
            result = executor.execute(
                _ctx(payload={"provider": "openai", "provider_config": {"model": "gpt-4o"}})
            )

        mock_gp.assert_called_once_with("openai", model="gpt-4o")
        assert isinstance(result, AnalyzeResult)

    def test_model_info_uses_provider_name_attribute(self):
        mock_provider = MagicMock()
        mock_provider.name = "openai"
        mock_provider.run.return_value = []

        with patch("apps.intelligence.providers.get_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider": "openai"}))

        assert result.model_info == {"provider": "openai"}


class TestAnalyzeExecutorActiveProvider(TestCase):
    """When payload has no 'provider', get_active_provider() is used."""

    def test_no_provider_key_calls_get_active_provider(self):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_active_provider", return_value=mock_provider
        ) as mock_gap:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}))

        mock_gap.assert_called_once()
        assert isinstance(result, AnalyzeResult)
        assert not result.errors

    def test_no_provider_key_with_provider_config(self):
        mock_provider = MagicMock()
        mock_provider.name = "claude"
        mock_provider.run.return_value = []

        with patch(
            "apps.intelligence.providers.get_active_provider", return_value=mock_provider
        ) as mock_gap:
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={"provider_config": {"model": "claude-opus"}}))

        mock_gap.assert_called_once_with(model="claude-opus")
        assert result.model_info == {"provider": "claude"}

    def test_model_info_fallback_when_provider_has_no_name(self):
        """When provider has no .name attr and no explicit provider_name, fallback is 'local'."""
        mock_provider = MagicMock(spec=[])  # no attributes at all
        mock_provider.run = MagicMock(return_value=[])

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}))

        assert result.model_info == {"provider": "local"}


class TestAnalyzeExecutorRecommendations(TestCase):
    """Tests for recommendation population and ai_output_ref."""

    def _execute_with_recs(self, recs):
        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = recs

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            return executor.execute(_ctx(payload={}))

    def test_ai_output_ref_is_set(self):
        result = self._execute_with_recs([])
        assert result.ai_output_ref == "intelligence:trace-abc:run-xyz:analyze"

    def test_empty_recommendations(self):
        result = self._execute_with_recs([])
        assert result.recommendations == []

    def test_recommendation_with_to_dict(self):
        """Objects with to_dict() are serialized via to_dict()."""
        rec = MagicMock()
        rec.to_dict.return_value = {"title": "Fix it", "priority": "high"}
        result = self._execute_with_recs([rec])
        assert result.recommendations == [{"title": "Fix it", "priority": "high"}]

    def test_recommendation_plain_dict_passthrough(self):
        """Plain dicts are passed through as-is."""
        rec = {"title": "Disk full", "priority": "critical"}
        result = self._execute_with_recs([rec])
        assert result.recommendations == [rec]

    def test_recommendation_object_without_to_dict_uses_vars(self):
        """Objects without to_dict() use vars()."""

        class SimpleRec:
            def __init__(self):
                self.title = "mem leak"
                self.priority = "medium"

        result = self._execute_with_recs([SimpleRec()])
        assert result.recommendations[0]["title"] == "mem leak"

    def test_recommendation_non_iterable_fallback(self):
        """Objects with no __dict__ fall back to {'value': str(r)}."""
        result = self._execute_with_recs(["just a string"])
        assert result.recommendations == [{"value": "just a string"}]

    def test_recommendations_with_incident(self):
        from apps.alerts.models import Incident

        incident = Incident.objects.create(title="Test", severity="critical")
        rec = {"title": "Fix it", "priority": "high"}

        mock_provider = MagicMock()
        mock_provider.name = "local"
        mock_provider.run.return_value = [rec]

        with patch("apps.intelligence.providers.get_active_provider", return_value=mock_provider):
            executor = AnalyzeExecutor()
            result = executor.execute(_ctx(payload={}, incident_id=incident.id))

        assert result.recommendations == [{"title": "Fix it", "priority": "high"}]
        assert result.confidence == 0.8


class TestAnalyzeExecutorErrorHandling(SimpleTestCase):
    """Tests for error path with fallback_enabled / fallback_disabled."""

    def test_error_with_fallback_enabled(self):
        with patch(
            "apps.intelligence.providers.get_active_provider",
            side_effect=RuntimeError("provider down"),
        ):
            executor = AnalyzeExecutor(fallback_enabled=True)
            result = executor.execute(_ctx(payload={}))

        assert result.fallback_used is True
        assert result.summary == "AI analysis unavailable"
        assert result.errors == []

    def test_error_with_fallback_disabled(self):
        with patch(
            "apps.intelligence.providers.get_active_provider",
            side_effect=RuntimeError("provider down"),
        ):
            executor = AnalyzeExecutor(fallback_enabled=False)
            result = executor.execute(_ctx(payload={}))

        assert result.fallback_used is False
        assert any("Analyze error" in e for e in result.errors)
