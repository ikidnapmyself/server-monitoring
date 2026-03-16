"""Tests for orchestration signals."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

import apps.orchestration.signals as signals_mod
from apps.orchestration.signals import (
    LoggingBackend,
    SignalTags,
    StageTimer,
    StatsdBackend,
    get_monitoring_backend,
)


def _make_tags(**overrides):
    defaults = {"trace_id": "t1", "run_id": "r1", "stage": "ingest"}
    defaults.update(overrides)
    return SignalTags(**defaults)


class SignalTagsTests(SimpleTestCase):
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


class StatsdBackendInitTests(SimpleTestCase):
    """Test StatsdBackend.__init__."""

    def test_init_defaults(self):
        backend = StatsdBackend()
        assert backend.host == "localhost"
        assert backend.port == 8125
        assert backend.prefix == "pipeline"
        assert backend._client is None

    def test_init_custom(self):
        backend = StatsdBackend(host="metrics.local", port=9000, prefix="myapp")
        assert backend.host == "metrics.local"
        assert backend.port == 9000
        assert backend.prefix == "myapp"


class StatsdGetClientTests(SimpleTestCase):
    """Test StatsdBackend._get_client."""

    def test_get_client_lazy_init(self):
        backend = StatsdBackend()
        mock_statsd = MagicMock()
        mock_client = MagicMock()
        mock_statsd.StatsClient.return_value = mock_client

        with patch.dict("sys.modules", {"statsd": mock_statsd}):
            client = backend._get_client()

        assert client is mock_client
        mock_statsd.StatsClient.assert_called_once_with("localhost", 8125, prefix="pipeline")

    def test_get_client_returns_cached(self):
        backend = StatsdBackend()
        sentinel = object()
        backend._client = sentinel
        assert backend._get_client() is sentinel

    def test_get_client_import_error(self):
        backend = StatsdBackend()
        # Remove statsd from sys.modules so import fails
        with patch.dict("sys.modules", {"statsd": None}):
            client = backend._get_client()
        assert client is None


class StatsdEmitTests(SimpleTestCase):
    """Test StatsdBackend.emit."""

    def test_emit_fallback_when_no_client(self):
        backend = StatsdBackend()
        backend._get_client = MagicMock(return_value=None)
        tags = _make_tags()

        with patch.object(LoggingBackend, "emit") as mock_log_emit:
            backend.emit("test.signal", tags, value=1.0)
            mock_log_emit.assert_called_once()

    def test_emit_timing_for_duration(self):
        backend = StatsdBackend()
        mock_client = MagicMock()
        backend._client = mock_client
        tags = _make_tags(source="grafana")

        backend.emit("pipeline.stage.duration", tags, value=42.0)
        mock_client.timing.assert_called_once_with("pipeline.stage.duration.ingest.grafana", 42.0)

    def test_emit_gauge_for_non_duration_value(self):
        backend = StatsdBackend()
        mock_client = MagicMock()
        backend._client = mock_client
        tags = _make_tags(source="grafana")

        backend.emit("pipeline.stage.failure_count", tags, value=1.0)
        mock_client.gauge.assert_called_once_with(
            "pipeline.stage.failure_count.ingest.grafana", 1.0
        )

    def test_emit_incr_when_no_value(self):
        backend = StatsdBackend()
        mock_client = MagicMock()
        backend._client = mock_client
        tags = _make_tags(source="grafana")

        backend.emit("pipeline.stage.started", tags)
        mock_client.incr.assert_called_once_with("pipeline.stage.started.ingest.grafana")


class LoggingBackendTests(SimpleTestCase):
    """Test LoggingBackend.emit."""

    def test_emit_with_value_and_extra(self):
        backend = LoggingBackend()
        tags = _make_tags()
        backend.emit("test.signal", tags, value=1.5, extra={"key": "val"})

    def test_emit_without_value_or_extra(self):
        backend = LoggingBackend()
        tags = _make_tags()
        backend.emit("test.signal", tags)


class GetBackendTests(SimpleTestCase):
    """Test _get_backend lazy init."""

    def setUp(self):
        signals_mod._backend = None

    def tearDown(self):
        signals_mod._backend = None

    def test_lazy_init_returns_logging_by_default(self):
        backend = signals_mod._get_backend()
        assert isinstance(backend, LoggingBackend)
        # Second call returns same instance
        assert signals_mod._get_backend() is backend


class EmitHelperTests(SimpleTestCase):
    """Test the module-level emit_* helper functions."""

    def setUp(self):
        signals_mod._backend = MagicMock()

    def tearDown(self):
        signals_mod._backend = None

    def test_emit_stage_started(self):
        tags = _make_tags()
        signals_mod.emit_stage_started(tags)
        signals_mod._backend.emit.assert_called_once()

    def test_emit_stage_succeeded(self):
        tags = _make_tags()
        signals_mod.emit_stage_succeeded(tags, 100.0)
        assert signals_mod._backend.emit.call_count == 2

    def test_emit_stage_failed(self):
        tags = _make_tags()
        signals_mod.emit_stage_failed(tags, "RuntimeError", "boom", True, 50.0)
        assert signals_mod._backend.emit.call_count == 3

    def test_emit_stage_retrying(self):
        tags = _make_tags()
        signals_mod.emit_stage_retrying(tags)
        assert signals_mod._backend.emit.call_count == 2

    def test_emit_pipeline_started(self):
        tags = _make_tags()
        signals_mod.emit_pipeline_started(tags)
        signals_mod._backend.emit.assert_called_once()

    def test_emit_pipeline_completed(self):
        tags = _make_tags()
        signals_mod.emit_pipeline_completed(tags, 200.0, "succeeded")
        assert signals_mod._backend.emit.call_count == 2


class GetMonitoringBackendTests(SimpleTestCase):
    """Test get_monitoring_backend factory."""

    def test_default_returns_logging(self):
        backend = get_monitoring_backend()
        assert isinstance(backend, LoggingBackend)

    @override_settings(ORCHESTRATION_METRICS_BACKEND="statsd")
    def test_statsd_backend(self):
        backend = get_monitoring_backend()
        assert isinstance(backend, StatsdBackend)

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="statsd",
        STATSD_HOST="m.local",
        STATSD_PORT=9999,
        STATSD_PREFIX="custom",
    )
    def test_statsd_backend_custom_settings(self):
        backend = get_monitoring_backend()
        assert isinstance(backend, StatsdBackend)
        assert backend.host == "m.local"
        assert backend.port == 9999
        assert backend.prefix == "custom"


class StageTimerTests(SimpleTestCase):
    """Test StageTimer context manager."""

    def setUp(self):
        signals_mod._backend = None

    def tearDown(self):
        signals_mod._backend = None

    def test_init(self):
        tags = _make_tags()
        cb = MagicMock()
        timer = StageTimer(tags, on_success=cb)
        assert timer.tags is tags
        assert timer.on_success is cb
        assert timer.start_time == 0.0
        assert timer.duration_ms == 0.0

    @patch("apps.orchestration.signals._get_backend")
    def test_success_path(self, mock_get_backend):
        mock_be = MagicMock()
        mock_get_backend.return_value = mock_be
        tags = _make_tags()
        callback = MagicMock()

        with StageTimer(tags, on_success=callback) as timer:
            pass

        assert timer.duration_ms > 0
        callback.assert_called_once_with(timer.duration_ms)
        # stage_started + stage_succeeded + duration = 3 emit calls
        assert mock_be.emit.call_count == 3

    @patch("apps.orchestration.signals._get_backend")
    def test_success_no_callback(self, mock_get_backend):
        mock_be = MagicMock()
        mock_get_backend.return_value = mock_be
        tags = _make_tags()

        with StageTimer(tags):
            pass

        # stage_started + stage_succeeded + duration = 3 emit calls
        assert mock_be.emit.call_count == 3

    @patch("apps.orchestration.signals._get_backend")
    def test_failure_path_retryable(self, mock_get_backend):
        mock_be = MagicMock()
        mock_get_backend.return_value = mock_be
        tags = _make_tags()

        with self.assertRaises(RuntimeError):
            with StageTimer(tags):
                raise RuntimeError("boom")

        # started + failed + duration + failure_count = 4 calls
        assert mock_be.emit.call_count == 4
        # Check the failed call has retryable=True (RuntimeError is retryable)
        failed_call = mock_be.emit.call_args_list[1]
        assert failed_call[1]["extra"]["retryable"] is True
        assert failed_call[1]["extra"]["error_type"] == "RuntimeError"

    @patch("apps.orchestration.signals._get_backend")
    def test_failure_path_non_retryable(self, mock_get_backend):
        mock_be = MagicMock()
        mock_get_backend.return_value = mock_be
        tags = _make_tags()

        with self.assertRaises(ValueError):
            with StageTimer(tags):
                raise ValueError("bad input")

        failed_call = mock_be.emit.call_args_list[1]
        assert failed_call[1]["extra"]["retryable"] is False
