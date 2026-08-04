import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.alerts.models import Alert
from apps.orchestration.models import PipelineRun, PipelineStatus


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookViewTests(TestCase):
    """The webhook durably records a PENDING run and returns 202 (no inline work)."""

    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("alerts:webhook")

    def test_webhook_records_pending_run_and_returns_202(self):
        payload = {"name": "Test Alert", "status": "firing", "severity": "warning"}

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "accepted")
        run = PipelineRun.objects.get(run_id=data["run_id"])
        self.assertEqual(run.status, PipelineStatus.PENDING)
        # Stored in the wrapper shape IngestExecutor expects ({driver, payload}).
        self.assertEqual(run.inbound_payload, {"driver": None, "payload": payload})
        # Nothing was processed inline: no Alert, no stage executions.
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(run.stage_executions.count(), 0)

    def test_webhook_post_invalid_json(self):
        response = self.client.post(
            self.webhook_url,
            data="not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_webhook_get_health_check(self):
        response = self.client.get(self.webhook_url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_webhook_with_driver_records_source(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
        payload = {"name": "Test Alert", "status": "firing"}

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        run = PipelineRun.objects.get(run_id=response.json()["run_id"])
        self.assertEqual(run.source, "generic")

    def test_webhook_unknown_driver_returns_400_without_recording(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "nope"})
        response = self.client.post(
            url,
            data=json.dumps({"name": "x"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PipelineRun.objects.count(), 0)

    @patch("apps.orchestration.orchestrator.PipelineOrchestrator.start_pipeline")
    def test_webhook_unexpected_error_returns_500(self, mock_start):
        mock_start.side_effect = RuntimeError("db down")
        response = self.client.post(
            self.webhook_url,
            data=json.dumps({"name": "x", "status": "firing"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["status"], "error")


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookSkipCheckersTests(TestCase):
    """A driver's skip_checkers is captured in the recorded run's payload."""

    def setUp(self):
        self.client = Client()

    def test_cluster_webhook_captures_skip_checkers(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        payload = {"source": "cluster", "instance_id": "web-03", "alerts": []}

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 202)
        run = PipelineRun.objects.get(run_id=response.json()["run_id"])
        self.assertTrue(run.inbound_payload.get("skip_checkers"))

    def test_non_cluster_webhook_does_not_set_skip_checkers(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
        payload = {"name": "x", "status": "firing"}

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 202)
        run = PipelineRun.objects.get(run_id=response.json()["run_id"])
        self.assertNotIn("skip_checkers", run.inbound_payload)
