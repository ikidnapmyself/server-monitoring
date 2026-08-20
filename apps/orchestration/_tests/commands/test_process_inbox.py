from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.orchestration.models import PipelineDefinition, PipelineRun, PipelineStatus
from apps.orchestration.orchestrator import PipelineOrchestrator


class ProcessInboxTests(TestCase):
    def setUp(self):
        # An all-flags-false catch-all means a drained run does ONLY ingest, so the
        # drain is deterministic and fast (no real checkers/intelligence/notify).
        PipelineDefinition.objects.create(
            name="ingest-only",
            match=[],
            priority=1,
            stages=[],
        )

    def _pending(self, name="x"):
        return PipelineOrchestrator().start_pipeline(
            payload={"driver": "generic", "payload": {"name": name, "status": "firing"}},
            source="generic",
        )

    def test_drains_a_pending_run_to_ingested(self):
        from apps.alerts.models import Alert

        run = self._pending()
        call_command("process_inbox", "--limit", "10")
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.INGESTED)
        self.assertEqual(Alert.objects.count(), 1)  # ingest actually ran via the stored wrapper

    def test_limit_bounds_the_pass(self):
        """--limit bounds how many INBOUND runs a pass claims.

        Counted by run_id rather than by rows: a drained push enqueues its own
        downstream runs, which are PENDING too and are legitimately left for the
        next pass.
        """
        pending = [self._pending(name=f"a{i}") for i in range(3)]

        call_command("process_inbox", "--limit", "1")

        still_pending = set(
            PipelineRun.objects.filter(status=PipelineStatus.PENDING).values_list(
                "run_id", flat=True
            )
        )
        assert len({run.run_id for run in pending} & still_pending) == 2

    def test_id_targets_one_run(self):
        r1, r2 = self._pending("r1"), self._pending("r2")
        call_command("process_inbox", "--id", r1.run_id)
        r1.refresh_from_db()
        r2.refresh_from_db()
        self.assertEqual(r1.status, PipelineStatus.INGESTED)
        self.assertEqual(r2.status, PipelineStatus.PENDING)

    def test_unknown_id_raises(self):
        with self.assertRaises(CommandError):
            call_command("process_inbox", "--id", "no-such-run")

    def test_id_non_pending_is_skipped(self):
        run = self._pending()
        PipelineRun.objects.filter(pk=run.pk).update(status=PipelineStatus.PROCESSING)
        call_command("process_inbox", "--id", run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.PROCESSING)  # not re-claimed

    def test_fresh_processing_run_is_not_touched(self):
        run = self._pending()
        PipelineRun.objects.filter(pk=run.pk).update(status=PipelineStatus.PROCESSING)
        call_command("process_inbox", "--limit", "10")
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.PROCESSING)

    def test_stale_processing_is_reclaimed_and_drained(self):
        run = self._pending()
        PipelineRun.objects.filter(pk=run.pk).update(status=PipelineStatus.PROCESSING)
        PipelineRun.objects.filter(pk=run.pk).update(
            updated_at=timezone.now() - timedelta(minutes=30)
        )
        call_command("process_inbox", "--limit", "10", "--stale-minutes", "15")
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.INGESTED)

    def test_lost_claim_is_skipped(self):
        # Simulate a concurrent drain winning the claim: the shared claim() returns False.
        run = self._pending()
        with patch("apps.orchestration.inbox.claim", return_value=False):
            call_command("process_inbox", "--limit", "10")
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.PENDING)

    def test_loop_drains_then_stops_on_interrupt(self):
        run = self._pending()
        with patch(
            "apps.orchestration.management.commands.process_inbox.time.sleep",
            side_effect=KeyboardInterrupt,
        ):
            call_command("process_inbox", "--loop", "--interval", "0")
        run.refresh_from_db()
        self.assertEqual(run.status, PipelineStatus.INGESTED)


class WebhookToDrainIntegrationTests(TestCase):
    """End-to-end: the webhook's stored wrapper is processable by the drain."""

    def setUp(self):
        PipelineDefinition.objects.create(
            name="ingest-only",
            match=[],
            priority=1,
            stages=[],
        )

    def test_webhook_then_process_inbox_creates_alert(self):
        import json

        from django.test import override_settings
        from django.urls import reverse

        from apps.alerts.models import Alert

        url = reverse("alerts:webhook_driver", kwargs={"driver": "generic"})
        with override_settings(API_KEY_AUTH_ENABLED=False):
            resp = self.client.post(
                url,
                data=json.dumps({"name": "cpu high", "status": "firing", "severity": "critical"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 202)
        run_id = resp.json()["run_id"]

        # Nothing processed yet.
        self.assertEqual(Alert.objects.count(), 0)

        call_command("process_inbox", "--limit", "10")

        self.assertEqual(Alert.objects.count(), 1)
        run = PipelineRun.objects.get(run_id=run_id)
        self.assertEqual(run.status, PipelineStatus.INGESTED)
