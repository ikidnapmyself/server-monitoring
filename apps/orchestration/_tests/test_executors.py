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


class TestCheckExecutorHostnameAndNoIncidents(SimpleTestCase):
    def test_check_executor_passes_hostname_and_no_incidents(self):
        """CheckExecutor passes hostname and no_incidents to CheckAlertBridge."""
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=1,
            errors=[],
            check_results=[],
        )

        ctx = StageContext(
            trace_id="t",
            run_id="r",
            payload={
                "hostname": "web-01",
                "no_incidents": True,
                "checker_names": ["cpu"],
            },
        )

        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            return_value=mock_bridge,
        ) as mock_cls:
            executor = CheckExecutor()
            executor.execute(ctx)

        mock_cls.assert_called_once_with(
            hostname="web-01",
            auto_create_incidents=False,
        )


class TestCheckExecutorError(SimpleTestCase):
    def test_exception_captured(self):
        with patch(
            "apps.alerts.check_integration.CheckAlertBridge",
            side_effect=RuntimeError("bridge broken"),
        ):
            result = CheckExecutor().execute(_ctx())

        assert any("Check error" in e for e in result.errors)
        assert result.duration_ms > 0


class TestCheckExecutorAllConfigExpansion(SimpleTestCase):
    """CheckExecutor expands the special '__all__' checker_configs key."""

    def _make_bridge(self):
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = MagicMock(
            checks_run=2, errors=[], check_results=[]
        )
        return mock_bridge

    def test_all_key_expands_to_per_checker_entries_for_all_registry(self):
        """When checker_names is None, '__all__' expands to every registry checker."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_configs": {
                    "__all__": {"warning_threshold": 60.0, "critical_threshold": 80.0}
                },
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        # "__all__" must be gone; per-checker entries must be present
        assert "__all__" not in configs
        assert configs["cpu"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}
        assert configs["memory"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}

    def test_all_key_expands_only_to_specified_checker_names(self):
        """When checker_names is given, '__all__' expands only to those checkers."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock(), "disk": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_names": ["cpu", "disk"],
                "checker_configs": {"__all__": {"warning_threshold": 70.0}},
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        assert "__all__" not in configs
        assert configs["cpu"] == {"warning_threshold": 70.0}
        assert configs["disk"] == {"warning_threshold": 70.0}
        assert "memory" not in configs

    def test_all_key_merges_with_existing_per_checker_config(self):
        """Per-checker config overrides the __all__ defaults (checker-specific wins)."""
        mock_bridge = self._make_bridge()
        fake_registry = {"cpu": MagicMock(), "memory": MagicMock()}

        ctx = _ctx(
            payload={
                "checker_configs": {
                    "__all__": {"warning_threshold": 60.0, "critical_threshold": 80.0},
                    "cpu": {"warning_threshold": 50.0},  # overrides __all__ for cpu
                },
            }
        )

        with (
            patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge),
            patch("apps.checkers.checkers.CHECKER_REGISTRY", fake_registry),
        ):
            CheckExecutor().execute(ctx)

        _, call_kwargs = mock_bridge.run_checks_and_alert.call_args
        configs = call_kwargs["checker_configs"]

        assert "__all__" not in configs
        # cpu had an existing entry; __all__ fills in critical_threshold but warning_threshold
        # stays at 50.0 (checker-specific value wins)
        assert configs["cpu"]["warning_threshold"] == 50.0
        assert configs["cpu"]["critical_threshold"] == 80.0
        # memory gets the full __all__ defaults
        assert configs["memory"] == {"warning_threshold": 60.0, "critical_threshold": 80.0}


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
        # Channel config templates are trusted (DB-sourced) but must use the
        # dict form to be rendered as an inline Jinja2 template.
        channel_obj.config = {
            "template": {"type": "inline", "template": "Hello {{ title }}"},
        }

        resolve_ret = ("slack", {}, "slack", driver_cls, channel_obj, "default")

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=resolve_ret,
        ):
            result = NotifyExecutor().execute(_ctx(payload={}, previous_results={"analyze": {}}))

        assert not result.errors
        msg = result.messages[0]
        assert "Hello Incident Analysis" in msg["message"]

    def test_payload_config_template_is_ignored(self):
        """Templates passed in pipeline payload.notify_config are IGNORED.

        SSTI hardening: only DB-sourced channel config may provide a template.
        Untrusted payload templates must never be rendered. When payload
        contains a template but no channel_obj does, the executor falls back
        to the default ``build_notification_body`` output.
        """
        driver_cls, _ = _mock_driver_cls()

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={
                        # This template must NOT be rendered; the executor
                        # ignores payload-sourced templates entirely.
                        "notify_config": {"template": "Payload: {{ title }}"},
                    },
                    previous_results={"analyze": {}},
                )
            )

        assert not result.errors
        msg = result.messages[0]
        # The payload-supplied literal string must not appear anywhere; the
        # executor must use the default build_notification_body output.
        assert "Payload: {{ title }}" not in msg["message"]
        assert "Payload: Incident Analysis" not in msg["message"]
        # Default body is produced from build_notification_body; assert it
        # produced non-empty content so we know the fallback path ran.
        assert msg["message"]

    def test_template_render_error_falls_back(self):
        driver_cls, _ = _mock_driver_cls()
        channel_obj = MagicMock()
        # Broken inline template in channel config: render error should be
        # swallowed and the executor should fall back to the default body.
        channel_obj.config = {
            "template": {"type": "inline", "template": "{{ bad }"},
        }

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

    def test_payload_ssti_attempt_ignored(self):
        """SSTI regression: a malicious inline template in payload is ignored.

        This is the positive security test that complements
        ``test_payload_config_template_is_ignored``. An attacker-controlled
        payload with a Jinja2 expression must never be rendered.
        """
        driver_cls, _ = _mock_driver_cls()

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            return_value=_resolve_return(driver_cls),
        ):
            result = NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_config": {"template": "{{ 7*7 }}"},
                    },
                    previous_results={"analyze": {}},
                )
            )

        assert not result.errors
        msg = result.messages[0]
        # The expression must not be evaluated: '49' must not appear, nor the
        # raw template source.
        assert "49" not in msg["message"]
        assert "{{ 7*7 }}" not in msg["message"]

    def test_payload_config_template_keys_stripped_before_resolve(self):
        """Template keys are removed from payload_config before NotifySelector.resolve().

        Drivers receive a config dict without template keys when config originates
        from the payload (no DB channel). This closes the path where a driver
        calling render_message_templates() could pick up payload-supplied template
        values and render attacker-controlled Jinja2 source.
        """
        driver_cls, _ = _mock_driver_cls()
        captured_payload_config = {}

        def _capture_resolve(provider_arg, payload_config=None, requested_channel=None):
            captured_payload_config.update(payload_config or {})
            return _resolve_return(driver_cls)

        with patch(
            "apps.notify.services.NotifySelector.resolve",
            side_effect=_capture_resolve,
        ):
            NotifyExecutor().execute(
                _ctx(
                    payload={
                        "notify_config": {
                            "template": "{{ 7*7 }}",
                            "payload_template": "bad",
                            "html_template": "<b>evil</b>",
                            "text_template": "also bad",
                            "webhook_url": "http://legit.example.com",
                        },
                    },
                    previous_results={"analyze": {}},
                )
            )

        # Template keys must have been stripped; non-template keys must be kept
        assert "template" not in captured_payload_config
        assert "payload_template" not in captured_payload_config
        assert "html_template" not in captured_payload_config
        assert "text_template" not in captured_payload_config
        assert captured_payload_config.get("webhook_url") == "http://legit.example.com"


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
