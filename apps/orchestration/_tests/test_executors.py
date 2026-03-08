"""Tests for all stage executors (apps/orchestration/executors.py).

Covers IngestExecutor, CheckExecutor, AnalyzeExecutor, and NotifyExecutor.
"""

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from apps.orchestration.dtos import (
    AnalyzeResult,
    CheckResult,
    IngestResult,
    NotifyResult,
    StageContext,
)
from apps.orchestration.executors import (
    AnalyzeExecutor,
    CheckExecutor,
    IngestExecutor,
    NotifyExecutor,
)


def _ctx(payload=None, incident_id=None, previous_results=None, source="test"):
    """Build a minimal StageContext."""
    return StageContext(
        trace_id="trace-abc",
        run_id="run-xyz",
        incident_id=incident_id,
        payload=payload or {},
        previous_results=previous_results or {},
        source=source,
    )


# ── IngestExecutor ────────────────────────────────────────────────────────


class TestIngestExecutorSuccess(TestCase):
    def test_successful_ingest(self):
        @dataclass
        class FakeResult:
            alerts_created: int = 1
            alerts_updated: int = 0
            alerts_resolved: int = 0
            incidents_created: int = 1
            incidents_updated: int = 0
            errors: list = field(default_factory=list)

        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = FakeResult()

        mock_alert = MagicMock()
        mock_alert.incident_id = 42
        mock_alert.fingerprint = "fp-123"
        mock_alert.severity = "critical"

        with (
            patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch),
            patch(
                "apps.alerts.models.Alert.objects.order_by",
                return_value=MagicMock(
                    select_related=MagicMock(
                        return_value=MagicMock(first=MagicMock(return_value=mock_alert))
                    )
                ),
            ),
        ):
            result = IngestExecutor().execute(
                _ctx(payload={"driver": "generic", "payload": {"key": "val"}})
            )

        assert isinstance(result, IngestResult)
        assert result.alerts_created == 1
        assert result.incidents_created == 1
        assert result.incident_id == 42
        assert result.severity == "critical"
        assert result.source == "test"
        assert result.normalized_payload_ref == "payload:trace-abc:run-xyz:ingest"
        assert result.duration_ms > 0

    def test_invalid_payload_not_dict(self):
        result = IngestExecutor().execute(
            _ctx(payload={"driver": "generic", "payload": "not a dict"})
        )
        assert "payload must be a JSON object" in result.errors

    def test_missing_payload_key(self):
        result = IngestExecutor().execute(_ctx(payload={"driver": "generic"}))
        assert "payload must be a JSON object" in result.errors

    def test_no_latest_alert(self):
        @dataclass
        class FakeResult:
            alerts_created: int = 0
            alerts_updated: int = 0
            alerts_resolved: int = 0
            incidents_created: int = 0
            incidents_updated: int = 0
            errors: list = field(default_factory=list)

        mock_orch = MagicMock()
        mock_orch.process_webhook.return_value = FakeResult()

        with (
            patch("apps.alerts.services.AlertOrchestrator", return_value=mock_orch),
            patch(
                "apps.alerts.models.Alert.objects.order_by",
                return_value=MagicMock(
                    select_related=MagicMock(
                        return_value=MagicMock(first=MagicMock(return_value=None))
                    )
                ),
            ),
        ):
            result = IngestExecutor().execute(_ctx(payload={"driver": "generic", "payload": {}}))

        assert result.incident_id is None
        assert not result.errors


class TestIngestExecutorError(SimpleTestCase):
    def test_exception_captured(self):
        with patch(
            "apps.alerts.services.AlertOrchestrator",
            side_effect=RuntimeError("boom"),
        ):
            result = IngestExecutor().execute(_ctx(payload={"driver": "generic", "payload": {}}))

        assert any("Ingest error" in e for e in result.errors)
        assert result.duration_ms > 0


# ── CheckExecutor ─────────────────────────────────────────────────────────


class TestCheckExecutorSuccess(SimpleTestCase):
    def test_successful_check(self):
        @dataclass
        class FakeBridgeResult:
            checks_run: int = 3
            errors: list = field(default_factory=list)

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = FakeBridgeResult()

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx(payload={"checker_names": ["cpu", "memory"]}))

        assert isinstance(result, CheckResult)
        assert result.checks_run == 3
        assert result.checks_passed == 3
        assert result.checks_failed == 0
        assert result.checker_output_ref == "checker:trace-abc:run-xyz:check"

    def test_check_with_errors(self):
        @dataclass
        class FakeBridgeResult:
            checks_run: int = 2
            errors: list = field(default_factory=lambda: ["cpu failed"])

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = FakeBridgeResult()

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert result.checks_failed == 1
        assert result.checks_passed == 1

    def test_check_results_with_structured_checks(self):
        @dataclass
        class FakeCheck:
            name: str = "cpu"
            status: str = "ok"
            value: float = 45.2

        @dataclass
        class FakeBridgeResult:
            checks_run: int = 1
            errors: list = field(default_factory=list)
            check_results: list = field(default_factory=lambda: [FakeCheck()])

        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = FakeBridgeResult()

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ):
            result = CheckExecutor().execute(_ctx())

        assert len(result.checks) == 1
        assert result.checks[0]["name"] == "cpu"
        assert result.checks[0]["status"] == "ok"


class TestCheckExecutorError(SimpleTestCase):
    def test_exception_captured(self):
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            side_effect=RuntimeError("bridge broken"),
        ):
            result = CheckExecutor().execute(_ctx())

        assert any("Check error" in e for e in result.errors)
        assert result.duration_ms > 0


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


# ── NotifyExecutor ────────────────────────────────────────────────────────


def _mock_driver_cls(success=True, message_id="msg-1"):
    """Create a mock driver class that returns a configurable send result."""
    driver_instance = MagicMock()
    driver_instance.validate_config.return_value = True
    if success:
        driver_instance.send.return_value = {
            "success": True,
            "message_id": message_id,
        }
    else:
        driver_instance.send.return_value = {
            "success": False,
            "error": "delivery failed",
        }
    driver_cls = MagicMock(return_value=driver_instance)
    return driver_cls, driver_instance


def _resolve_return(driver_cls, channel="default"):
    """Build a NotifySelector.resolve() return tuple."""
    return ("slack", {"webhook_url": "http://x"}, "slack", driver_cls, None, channel)


class TestNotifyExecutorSuccess(TestCase):
    def test_successful_notification(self):
        driver_cls, driver_inst = _mock_driver_cls()
        previous = {
            "analyze": {
                "recommendations": [
                    {"title": "Fix CPU", "priority": "high", "description": "Too hot"}
                ]
            },
            "ingest": {"incident_id": 1, "severity": "critical"},
            "check": {"checks_run": 3, "checks_passed": 3, "checks_failed": 0},
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={"notify_driver": "slack"},
                    previous_results=previous,
                )
            )

        assert isinstance(result, NotifyResult)
        assert result.channels_attempted == 1
        assert result.channels_succeeded == 1
        assert result.channels_failed == 0
        assert not result.errors
        assert result.provider_ids == ["msg-1"]
        assert result.notify_output_ref == "notify:trace-abc:run-xyz:notify"
        assert len(result.messages) == 1
        driver_inst.send.assert_called_once()

    def test_severity_mapping_critical(self):
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "analyze": {
                "recommendations": [{"title": "A", "priority": "critical", "description": "x"}]
            }
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results=previous))

        msg = result.messages[0]
        assert msg["severity"] == "critical"

    def test_severity_mapping_warning(self):
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "analyze": {"recommendations": [{"title": "A", "priority": "high", "description": "x"}]}
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results=previous))

        msg = result.messages[0]
        assert msg["severity"] == "warning"

    def test_no_recommendations_defaults(self):
        driver_cls, _ = _mock_driver_cls()
        previous = {"analyze": {}}

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results=previous))

        msg = result.messages[0]
        assert msg["title"] == "Incident Analysis"
        assert msg["severity"] == "info"

    def test_fallback_used_message(self):
        driver_cls, _ = _mock_driver_cls()
        previous = {
            "analyze": {
                "fallback_used": True,
                "summary": "AI unavailable",
                "probable_cause": "Error",
                "actions": ["Check manually"],
            }
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results=previous))

        msg = result.messages[0]
        assert "AI Unavailable" in msg["title"]
        assert "AI analysis was unavailable" in msg["message"]


class TestNotifyExecutorDriverFailures(TestCase):
    def test_unknown_driver(self):
        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=("unknown", {}, "unknown", None, None, "default"),
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "unknown"}, previous_results={"analyze": {}})
            )

        assert result.channels_attempted == 0
        assert any("Unknown notify driver" in e for e in result.errors)

    def test_invalid_config(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.validate_config.return_value = False

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert any("Invalid configuration" in e for e in result.errors)

    def test_send_exception(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.side_effect = RuntimeError("connection refused")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert result.channels_failed == 1
        assert any("Send error" in e for e in result.errors)
        assert result.deliveries[0]["status"] == "failed"

    def test_send_returns_failure(self):
        driver_cls, _ = _mock_driver_cls(success=False)

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert result.channels_failed == 1
        assert result.channels_succeeded == 0
        assert result.deliveries[0]["status"] == "failed"


class TestNotifyExecutorTemplateRendering(TestCase):
    def test_template_from_channel_config(self):
        driver_cls, _ = _mock_driver_cls()
        channel_obj = MagicMock()
        channel_obj.config = {"template": "Hello {{ title }}"}

        resolve_ret = ("slack", {}, "slack", driver_cls, channel_obj, "default")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=resolve_ret,
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert not result.errors
        msg = result.messages[0]
        assert "Hello Incident Analysis" in msg["message"]

    def test_template_from_payload_config(self):
        driver_cls, _ = _mock_driver_cls()

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_config": {"template": "Payload: {{ title }}"},
                    },
                    previous_results={"analyze": {}},
                )
            )

        assert not result.errors
        msg = result.messages[0]
        assert "Payload: Incident Analysis" in msg["message"]

    def test_template_render_error_falls_back(self):
        driver_cls, _ = _mock_driver_cls()
        channel_obj = MagicMock()
        channel_obj.config = {"template": "{{ bad }"}

        resolve_ret = ("slack", {}, "slack", driver_cls, channel_obj, "default")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=resolve_ret,
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        # Falls back to build_notification_body — no error in result
        assert not result.errors
        msg = result.messages[0]
        assert msg["message"]  # non-empty fallback


class TestNotifyExecutorError(SimpleTestCase):
    def test_outer_exception_captured(self):
        with patch(
            "apps.notify.services.NotifySelector.resolve",
            side_effect=RuntimeError("selector broken"),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert any("Notify error" in e for e in result.errors)
        assert result.duration_ms > 0


class TestNotifyExecutorProviderIds(TestCase):
    def test_list_provider_ids(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": ["id-1", "id-2"],
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert result.provider_ids == ["id-1", "id-2"]

    def test_empty_provider_id_not_appended(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": "",
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert result.provider_ids == []
        assert result.channels_succeeded == 1

    def test_numeric_provider_id_coerced(self):
        driver_cls, driver_inst = _mock_driver_cls()
        driver_inst.send.return_value = {
            "success": True,
            "message_id": 12345,
        }

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert result.provider_ids == ["12345"]
