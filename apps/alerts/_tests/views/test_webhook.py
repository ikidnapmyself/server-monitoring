import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.alerts.models import Alert, Node
from apps.orchestration import inbox
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
)


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
class WebhookClusterLaneRoutingTests(TestCase):
    """Cluster traffic skips CHECK because the ``cluster-nodes`` lane says so.

    Was ``WebhookSkipCheckersTests``, which asserted the *mechanism* — a
    ``skip_checkers`` key the view copied off the driver. That key is gone; the
    outcome it produced is now a row migration ``0012`` seeds. So these tests
    assert the outcome instead, end to end: a real POST, a real drain, and real
    routing against the seeded rows. No stage list is passed in and no helper is
    called directly, so a lane that is configured but never consulted cannot pass
    them.
    """

    def setUp(self):
        self.client = Client()
        self.url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})

    def _push(self):
        """POST one firing cluster alert; return its PENDING run."""
        payload = {
            "source": "cluster",
            "instance_id": "web-03",
            "hostname": "web-03.example.com",
            "alerts": [
                {
                    "fingerprint": "cpu-web-03",
                    "name": "CPU usage critical",
                    "status": "firing",
                    "severity": "critical",
                    "labels": {"checker": "cpu"},
                }
            ],
        }
        response = self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 202)
        return PipelineRun.objects.get(run_id=response.json()["run_id"])

    def _drained_stages(self, run):
        """Stages that actually executed for ``run``, in execution order.

        The checker bridge is stubbed, and only the bridge. The POST, the drain,
        routing resolution and every ``StageExecution`` row still happen for real
        — CHECK included, which is the stage under test in the negative arm. What
        the stub removes is the I/O behind that stage: a real ``CheckExecutor``
        runs the whole ``CHECKER_REGISTRY`` against the machine running the
        tests (CPU sampling, disk scans, SMART and temperature probes), which
        cost 36s here and varies with the runner's disks and sensors. The three
        lanes that never reach CHECK pay nothing for the patch.
        """
        mock_bridge = MagicMock()
        mock_bridge.run_checks_and_alert.return_value = SimpleNamespace(checks_run=0, errors=[])
        with patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge):
            inbox.drain_run(run.run_id)
        return list(run.stage_executions.order_by("id").values_list("stage", flat=True))

    def test_wrapper_payload_carries_only_driver_and_payload(self):
        """The view no longer smuggles a routing decision into the wrapper."""
        run = self._push()
        self.assertEqual(set(run.inbound_payload), {"driver", "payload"})
        self.assertEqual(run.inbound_payload["driver"], "cluster")

    def test_drained_cluster_push_analyzes_and_notifies_but_never_checks(self):
        run = self._push()
        stages = self._drained_stages(run)
        self.assertEqual(
            stages,
            [PipelineStage.INGEST, PipelineStage.ANALYZE, PipelineStage.NOTIFY],
        )

    def test_the_cluster_lane_is_the_one_routing_resolved_to(self):
        """Read the stamp routing wrote, not the row's name from the table.

        A lane can be perfectly configured and never consulted; the incident's
        ``pipeline`` FK is written by ``_downstream_stages`` on the matched lane,
        so it is evidence that resolution reached this row.
        """
        run = self._push()
        self._drained_stages(run)
        incident = Alert.objects.get(fingerprint="cpu-web-03").incident
        self.assertIsNotNone(incident)
        self.assertEqual(incident.pipeline.name, "cluster-nodes")

    def test_deleting_the_cluster_lane_sends_node_traffic_through_check(self):
        """The accepted consequence of holding this rule purely as data.

        Nothing in the engine special-cases cluster, so removing the row removes
        the behaviour: the push falls through to the seeded catch-all and the hub
        runs its own checkers on node traffic. Useless output, visible in the
        admin, and an operator's choice to make.
        """
        PipelineDefinition.objects.filter(name="cluster-nodes").delete()
        run = self._push()
        stages = self._drained_stages(run)
        self.assertIn(PipelineStage.CHECK, stages)
        incident = Alert.objects.get(fingerprint="cpu-web-03").incident
        self.assertEqual(incident.pipeline.name, "catch-all")


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
