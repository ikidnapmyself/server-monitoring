import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.alerts.models import Alert, Node
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


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookNodeRegistrationTests(TestCase):
    """A cluster push registers/refreshes the sending node synchronously — at push
    time — so the node is visible the instant the 202 returns, independent of the
    drain. The payload's alerts are still processed later by the drain."""

    def setUp(self):
        self.client = Client()

    def test_cluster_push_registers_node_synchronously(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        payload = {
            "source": "cluster",
            "instance_id": "web-03",
            "hostname": "web-03.example.com",
            "alerts": [],
        }

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 202)
        # Node exists immediately, before any drain runs.
        node = Node.objects.get(instance_id="web-03")
        self.assertEqual(node.hostname, "web-03.example.com")
        self.assertEqual(node.last_source, "cluster")
        # Processing is still deferred: the run is PENDING with no alerts yet.
        run = PipelineRun.objects.get(run_id=response.json()["run_id"])
        self.assertEqual(run.status, PipelineStatus.PENDING)
        self.assertEqual(Alert.objects.count(), 0)

    def test_cluster_push_refreshes_existing_node(self):
        Node.objects.create(instance_id="web-03", hostname="old")
        url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        payload = {"source": "cluster", "instance_id": "web-03", "hostname": "new", "alerts": []}

        self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(Node.objects.count(), 1)
        self.assertEqual(Node.objects.get(instance_id="web-03").hostname, "new")

    def test_cluster_push_without_instance_id_registers_no_node(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        payload = {"source": "cluster", "alerts": []}

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Node.objects.count(), 0)

    def test_non_cluster_push_registers_no_node(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
        payload = {"name": "x", "status": "firing"}

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Node.objects.count(), 0)

    def test_webhook_run_origin_is_incoming_webhook(self):
        from apps.orchestration.models import PipelineOrigin

        payload = {"name": "Test Alert", "status": "firing", "severity": "warning"}
        response = self.client.post(
            reverse("alerts:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        run = PipelineRun.objects.get(run_id=response.json()["run_id"])
        self.assertEqual(run.origin, PipelineOrigin.INCOMING_WEBHOOK)
