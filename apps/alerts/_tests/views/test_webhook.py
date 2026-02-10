import json
import os
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.alerts.services import ProcessingResult


class WebhookViewTests(TestCase):
    """Tests for the webhook views."""

    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("alerts:webhook")

    def test_webhook_post_valid_payload(self):
        payload = {
            "name": "Test Alert",
            "status": "firing",
            "severity": "warning",
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        # Webhook returns 202 when Celery orchestration is enabled (queued for async processing)
        # or 200 when processing synchronously
        self.assertIn(response.status_code, [200, 202])
        data = response.json()
        if response.status_code == 202:
            self.assertEqual(data["status"], "queued")
            self.assertIn("pipeline_id", data)
        else:
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["alerts_created"], 1)

    def test_webhook_post_invalid_json(self):
        response = self.client.post(
            self.webhook_url,
            data="not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_webhook_get_health_check(self):
        response = self.client.get(self.webhook_url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_webhook_with_driver(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
        payload = {
            "name": "Test Alert",
            "status": "firing",
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        # Webhook returns 202 when Celery orchestration is enabled (queued for async processing)
        # or 200 when processing synchronously
        self.assertIn(response.status_code, [200, 202])


class WebhookViewPartialResponseTests(TestCase):
    """Tests for webhook partial responses when orchestrator reports errors."""

    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("alerts:webhook")

    @patch.dict(os.environ, {"ENABLE_CELERY_ORCHESTRATION": "0"})
    @patch("apps.alerts.views.AlertOrchestrator.process_webhook")
    def test_webhook_returns_partial_when_orchestrator_has_errors(self, mock_process):
        mock_process.return_value = ProcessingResult(errors=["boom"])

        response = self.client.post(
            self.webhook_url,
            data=json.dumps({"name": "x"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "partial")
        self.assertIn("errors", data)
