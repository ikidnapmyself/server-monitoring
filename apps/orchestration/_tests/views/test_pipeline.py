import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone


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


@override_settings(API_KEY_AUTH_ENABLED=False)
class TestPipelineView(TestCase):
    """The pipeline endpoint is a producer like any other.

    It ingests inline and then enqueues one run per materially changed incident,
    exactly as the alerts webhook does. ``/pipeline/sync/`` drains those runs
    before responding; ``/pipeline/`` leaves them PENDING for ``process_inbox``.
    """

    def setUp(self):
        self.client = Client()

    def _post(self, body, url="/orchestration/pipeline/"):
        return self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_invalid_json(self):
        """Invalid JSON body returns 400."""
        response = self.client.post(
            "/orchestration/pipeline/",
            data=b"not-json{{{",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid JSON body"

    def test_async_mode_enqueues_and_leaves_runs_pending(self):
        """Default mode enqueues one PENDING incident run and returns 202."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus

        response = self._post(
            {
                "payload": {"name": "CPU high", "status": "firing", "severity": "critical"},
                "source": "grafana",
                "environment": "staging",
            }
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert "run_id" not in data
        run = PipelineRun.objects.get()
        assert run.status == PipelineStatus.PENDING
        assert run.origin == PipelineOrigin.INCOMING_WEBHOOK
        assert run.source == "grafana"
        assert run.environment == "staging"
        assert run.incident_id == Alert.objects.get().incident_id
        assert data["trace_id"] == run.trace_id
        assert data["incidents"] == [run.incident_id]

    def test_sync_mode_leaves_nothing_pending(self):
        """Sync mode drains the runs it enqueued before responding."""
        from apps.orchestration.models import PipelineRun, PipelineStatus

        # The endpoint enqueues inside a transaction and drains on its commit, so
        # the drain never claims runs the transaction has not committed. TestCase
        # never commits, hence the capture.
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(
                {"payload": {"name": "CPU high", "status": "firing", "severity": "critical"}},
                url="/orchestration/pipeline/sync/",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["alerts"] == 1
        assert data["incidents"] == [PipelineRun.objects.get().incident_id]
        assert data["errors"] == []
        assert not PipelineRun.objects.filter(status=PipelineStatus.PENDING).exists()

    def test_alerts_exist_before_any_drain(self):
        """The ingest happens on the request thread, outside the pipeline."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import StageExecution

        response = self._post(
            {"payload": {"name": "CPU high", "status": "firing", "severity": "warning"}}
        )

        assert response.status_code == 202
        alert = Alert.objects.get()
        assert alert.name == "CPU high"
        assert alert.incident is not None
        assert StageExecution.objects.count() == 0

    def test_no_run_carries_a_driver_payload_wrapper(self):
        """The entry stage is gone: no run stores the request body to re-parse."""
        from apps.orchestration.models import PipelineRun

        self._post(
            {
                "payload": {"name": "CPU high", "status": "firing", "severity": "warning"},
                "source": "grafana",
            }
        )

        run = PipelineRun.objects.get()
        assert run.inbound_payload == {"downstream_incident_id": run.incident_id}

    def test_an_undetectable_driver_returns_400(self):
        """No driver claimed the payload; a retry would fail identically."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import PipelineRun

        response = self._post({"payload": {"not": "an alert payload"}})

        assert response.status_code == 400
        assert Alert.objects.count() == 0
        assert PipelineRun.objects.count() == 0

    def test_a_failure_after_the_driver_resolved_returns_500(self):
        """Our fault, not the sender's: 400 would silently discard the push."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import PipelineRun

        with patch(
            "apps.alerts.drivers.generic.GenericWebhookDriver.parse",
            side_effect=RuntimeError("transient hub bug"),
        ):
            response = self._post({"payload": {"name": "CPU high", "status": "firing"}})

        assert response.status_code == 500
        assert Alert.objects.count() == 0
        assert PipelineRun.objects.count() == 0

    def test_a_caller_supplied_trace_id_is_honoured(self):
        """A caller correlating its own push keeps its trace_id end to end."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import PipelineRun

        response = self._post(
            {
                "payload": {"name": "CPU high", "status": "firing", "severity": "warning"},
                "trace_id": "caller-trace-1",
            }
        )

        assert response.json()["trace_id"] == "caller-trace-1"
        assert PipelineRun.objects.get().trace_id == "caller-trace-1"
        assert Alert.objects.get().trace_id == "caller-trace-1"

    def test_a_payload_with_no_alerts_is_accepted_and_logged(self):
        """A misconfigured sender, not a failure: 202, no rows, a WARNING."""
        from apps.alerts.models import Alert
        from apps.orchestration.models import PipelineRun

        with self.assertLogs("apps.orchestration.views.pipeline", level="WARNING") as logs:
            response = self._post({"payload": {"alerts": []}, "driver": "generic"})

        assert response.status_code == 202
        assert response.json()["incidents"] == []
        assert Alert.objects.count() == 0
        assert PipelineRun.objects.count() == 0
        assert response.json()["trace_id"] in "\n".join(logs.output)

    def test_errors_alongside_written_alerts_are_logged_and_accepted(self):
        """A partial failure must not become a 5xx: the sender would retry it."""
        from apps.alerts.models import Alert
        from apps.alerts.services import ProcessingResult

        alert = Alert.objects.create(
            fingerprint="fp-1",
            source="generic",
            name="CPU high",
            severity="critical",
            started_at=timezone.now(),
        )
        result = ProcessingResult(
            alerts=[alert],
            material_alerts=[],
            errors=["one alert failed"],
            driver_resolved=True,
        )

        with (
            patch("apps.alerts.services.AlertOrchestrator.process_webhook", return_value=result),
            self.assertLogs("apps.orchestration.views.pipeline", level="ERROR") as logs,
        ):
            response = self._post({"payload": {"name": "CPU high", "status": "firing"}})

        assert response.status_code == 202
        assert "one alert failed" in "\n".join(logs.output)

    def test_an_unexpected_error_returns_500(self):
        with patch(
            "apps.alerts.services.AlertOrchestrator.process_webhook",
            side_effect=RuntimeError("db down"),
        ):
            response = self._post({"payload": {"name": "x", "status": "firing"}})

        assert response.status_code == 500

    def test_the_500_body_says_nothing_about_the_exception(self):
        """The body is a fixed string; the detail lives in the logged traceback.

        The exception surface here is driver detection plus the whole alert
        write, so ``str(e)`` would hand the caller database errors, settings
        paths and payload fragments. ``logger.exception`` keeps all of it.
        """
        with (
            patch(
                "apps.alerts.services.AlertOrchestrator.process_webhook",
                side_effect=RuntimeError("db down at /srv/secret/settings.py"),
            ),
            self.assertLogs("apps.orchestration.views.pipeline", level="ERROR") as logs,
        ):
            response = self._post({"payload": {"name": "x", "status": "firing"}})

        assert response.status_code == 500
        body = response.content.decode()
        assert "db down" not in body
        assert "secret" not in body
        assert "RuntimeError" not in body

        logged = "\n".join(logs.output)
        assert "db down at /srv/secret/settings.py" in logged
        assert "Traceback" in logged
