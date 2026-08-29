import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.alerts import views
from apps.alerts.models import Alert, Node
from apps.alerts.services import ProcessingResult
from apps.orchestration import inbox
from apps.orchestration.models import (
    PipelineDefinition,
    PipelineOrigin,
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    StageExecution,
)


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookViewTests(TestCase):
    """The webhook ingests inline, then enqueues one PENDING run per incident.

    Producing an alert is no longer a pipeline stage: the bounded alert write
    happens on the request thread, and only the incident work (check, analyze,
    notify) is left for the drain.
    """

    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("alerts:webhook")

    def _post(self, payload, url=None):
        return self.client.post(
            url or self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_alerts_exist_before_any_drain_runs(self):
        response = self._post({"name": "Test Alert", "status": "firing", "severity": "warning"})

        self.assertEqual(response.status_code, 202)
        alert = Alert.objects.get()
        self.assertEqual(alert.name, "Test Alert")
        self.assertIsNotNone(alert.incident)
        # No stage executed: the ingest happened outside the pipeline entirely.
        self.assertEqual(StageExecution.objects.count(), 0)

    def test_one_pending_run_per_material_incident(self):
        payload = {
            "alerts": [
                {"name": "CPU high", "status": "firing", "severity": "critical"},
                {"name": "Disk full", "status": "firing", "severity": "critical"},
            ]
        }

        response = self._post(payload)

        self.assertEqual(response.status_code, 202)
        runs = list(PipelineRun.objects.all())
        self.assertEqual(len(runs), 2)
        incident_ids = {a.incident_id for a in Alert.objects.all()}
        self.assertEqual({r.incident_id for r in runs}, incident_ids)
        for run in runs:
            self.assertEqual(run.status, PipelineStatus.PENDING)
            self.assertEqual(run.origin, PipelineOrigin.INCOMING_WEBHOOK)
            self.assertEqual(run.inbound_payload, {"downstream_incident_id": run.incident_id})
        # One ingest, one trace.
        self.assertEqual(len({r.trace_id for r in runs}), 1)
        self.assertEqual(response.json()["trace_id"], runs[0].trace_id)

    def test_no_run_carries_a_driver_payload_wrapper(self):
        """The ingest run is gone; nothing is recorded for the drain to parse."""
        self._post({"name": "Test Alert", "status": "firing", "severity": "warning"})

        self.assertEqual(PipelineRun.objects.count(), 1)
        for run in PipelineRun.objects.all():
            self.assertNotIn("driver", run.inbound_payload)
            self.assertNotIn("payload", run.inbound_payload)

    def test_still_returns_202_with_trace_id_and_incidents(self):
        response = self._post({"name": "Test Alert", "status": "firing", "severity": "warning"})

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "accepted")
        self.assertNotIn("run_id", data)
        self.assertEqual(data["incidents"], [Alert.objects.get().incident_id])
        self.assertEqual(data["trace_id"], Alert.objects.get().trace_id)

    def test_an_oversized_body_is_rejected_and_writes_nothing(self):
        """Refused before the parse, so not even the node upsert happens."""
        payload = {
            "source": "cluster",
            "instance_id": "web-03",
            "alerts": [],
            "pad": "x" * (views.MAX_PAYLOAD_BYTES + 1),
        }

        response = self._post(
            payload, url=reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(PipelineRun.objects.count(), 0)
        self.assertEqual(Node.objects.count(), 0)

    def test_a_payload_with_no_alerts_writes_no_run_and_logs(self):
        """A misconfigured sender, not a failure: 202, no rows, a WARNING."""
        with self.assertLogs("apps.alerts.views", level="WARNING") as logs:
            response = self._post(
                {"alerts": []}, url=reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["incidents"], [])
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(PipelineRun.objects.count(), 0)
        message = "\n".join(logs.output)
        self.assertIn("generic", message)
        self.assertIn(response.json()["trace_id"], message)

    def test_an_undetectable_driver_returns_400(self):
        """Nothing parsed and nothing written is the sender's problem to see."""
        response = self._post({"not": "an alert payload"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(PipelineRun.objects.count(), 0)

    def test_the_sending_node_is_still_registered(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})

        response = self._post(
            {
                "source": "cluster",
                "instance_id": "web-03",
                "hostname": "web-03.example.com",
                "alerts": [],
            },
            url=url,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(Node.objects.get(instance_id="web-03").hostname, "web-03.example.com")

    def test_errors_alongside_written_alerts_are_logged_and_accepted(self):
        """A partial failure must not become a 5xx: the sender would retry it."""
        alert = Alert.objects.create(
            fingerprint="fp-1",
            source="generic",
            name="CPU high",
            severity="critical",
            started_at=timezone.now(),
        )
        result = ProcessingResult(alerts=[alert], material_alerts=[], errors=["one alert failed"])

        with (
            patch("apps.alerts.services.AlertOrchestrator.process_webhook", return_value=result),
            self.assertLogs("apps.alerts.views", level="ERROR") as logs,
        ):
            response = self._post({"name": "CPU high", "status": "firing"})

        self.assertEqual(response.status_code, 202)
        self.assertIn("one alert failed", "\n".join(logs.output))

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

        response = self._post({"name": "Test Alert", "status": "firing"}, url=url)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(PipelineRun.objects.get().source, "generic")

    def test_webhook_unknown_driver_returns_400_without_recording(self):
        url = reverse("alerts:webhook_driver", kwargs={"driver": "nope"})

        response = self._post({"name": "x"}, url=url)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PipelineRun.objects.count(), 0)
        self.assertEqual(Alert.objects.count(), 0)

    @patch("apps.alerts.services.AlertOrchestrator.process_webhook")
    def test_webhook_unexpected_error_returns_500(self, mock_process):
        mock_process.side_effect = RuntimeError("db down")

        response = self._post({"name": "x", "status": "firing"})

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
        from apps.notify.models import NotificationChannel
        from apps.orchestration.seeding import enable_delivery

        self.client = Client()
        self.url = reverse("alerts:webhook_driver", kwargs={"driver": "cluster"})
        # The seed shapes the lanes by how the hub is configured, and a test
        # database has no channel — so the seeded lanes record rather than
        # deliver. These tests assert delivery end to end, so they have to be the
        # configured hub they describe: `enable_delivery` restores NOTIFY on the
        # seeded lanes and binds them. The generic driver treats an empty config
        # as a no-op, so NOTIFY runs for real without touching the network.
        channel = NotificationChannel.objects.create(
            name="ops", driver="generic", is_active=True, config={}
        )
        enable_delivery(PipelineDefinition, channel)

    def _push(self):
        """POST one firing cluster alert; return the trace it was ingested under."""
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
        return response.json()["trace_id"]

    def _drained_stages(self, trace_id):
        """Stages that actually executed under ``trace_id``, in execution order.

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
        mock_bridge.run_checks_and_alert.return_value = SimpleNamespace(
            checks_run=0, errors=[], alerts=[], material_alerts=[]
        )
        with patch("apps.alerts.check_integration.CheckAlertBridge", return_value=mock_bridge):
            # The push itself ingested; what is queued is one run per incident it
            # materially changed, and a lane stage may enqueue further work. Drain
            # until the queue is empty so this stays the end-to-end test it was.
            while inbox.drain(limit=10):
                pass
        return list(
            StageExecution.objects.filter(pipeline_run__trace_id=trace_id)
            .order_by("id")
            .values_list("stage", flat=True)
        )

    def test_the_queued_work_is_the_incident_not_the_payload(self):
        """The view records no ingest wrapper at all — only the incident to work."""
        trace_id = self._push()
        run = PipelineRun.objects.get(trace_id=trace_id)
        incident = Alert.objects.get(fingerprint="check:web-03:cpu").incident
        self.assertEqual(run.inbound_payload, {"downstream_incident_id": incident.id})
        self.assertEqual(run.source, "cluster")

    def test_drained_cluster_push_analyzes_and_notifies_but_never_checks(self):
        trace_id = self._push()
        stages = self._drained_stages(trace_id)
        # INGEST is gone from the run record: the webhook did it inline.
        self.assertEqual(stages, [PipelineStage.ANALYZE, PipelineStage.NOTIFY])

    def test_the_cluster_lane_is_the_one_routing_resolved_to(self):
        """Read the stamp routing wrote, not the row's name from the table.

        A lane can be perfectly configured and never consulted; the incident's
        ``pipeline`` FK is written by ``_downstream_stages`` on the matched lane,
        so it is evidence that resolution reached this row.
        """
        trace_id = self._push()
        self._drained_stages(trace_id)
        incident = Alert.objects.get(fingerprint="check:web-03:cpu").incident
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
        trace_id = self._push()
        stages = self._drained_stages(trace_id)
        self.assertIn(PipelineStage.CHECK, stages)
        incident = Alert.objects.get(fingerprint="check:web-03:cpu").incident
        self.assertEqual(incident.pipeline.name, "catch-all")


@override_settings(API_KEY_AUTH_ENABLED=False)
class WebhookNodeRegistrationTests(TestCase):
    """A cluster push registers/refreshes the sending node synchronously — at push
    time — so the node is visible the instant the 202 returns. It runs before the
    ingest and independently of it: a push proves the sender is alive whatever its
    alerts do (or fail to do)."""

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
        # The push carried no alerts, so it queued no work — the node is still
        # registered, which is the whole point of registering ahead of ingest.
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(PipelineRun.objects.count(), 0)

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
        payload = {"name": "Test Alert", "status": "firing", "severity": "warning"}
        self.client.post(
            reverse("alerts:webhook"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(PipelineRun.objects.get().origin, PipelineOrigin.INCOMING_WEBHOOK)
