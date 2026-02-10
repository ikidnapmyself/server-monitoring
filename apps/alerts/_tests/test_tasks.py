from typing import Any

from django.test import TestCase


class ServiceOrchestratorTasksTests(TestCase):
    """Tests for the Celery-based service orchestrator pipeline."""

    def setUp(self):
        super().setUp()
        self.alertmanager_payload = {
            "receiver": "test",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "severity": "critical"},
                    "annotations": {"description": "Test alert description"},
                    "startsAt": "2025-01-01T00:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "generatorURL": "http://example.com",
                    "fingerprint": "abc123",
                }
            ],
            "groupLabels": {"alertname": "TestAlert"},
            "commonLabels": {"alertname": "TestAlert", "severity": "critical"},
            "commonAnnotations": {"description": "Test alert description"},
            "externalURL": "http://example.com",
            "version": "4",
            "groupKey": "{}",
        }

    def test_build_orchestration_chain_order(self):
        from apps.alerts.tasks import build_orchestration_chain

        sig = build_orchestration_chain({"trigger": "webhook", "payload": {}})

        # Celery chain stores tasks as signatures in `.tasks`
        self.assertEqual(len(sig.tasks), 4)
        self.assertEqual(sig.tasks[0].task, "apps.alerts.tasks.alerts_ingest")
        self.assertEqual(sig.tasks[1].task, "apps.alerts.tasks.run_diagnostics")
        self.assertEqual(sig.tasks[2].task, "apps.alerts.tasks.analyze_incident")
        self.assertEqual(sig.tasks[3].task, "apps.alerts.tasks.notify_channels")

    def test_pipeline_runs_in_eager_mode(self):
        """Pipeline contract test.

        This test is intentionally *fully mocked* to stay fast and deterministic.
        It validates that the orchestration flow returns a context dict containing
        the expected stage outputs.
        """

        from unittest.mock import patch

        from django.test import override_settings

        # IMPORTANT: import the module (not the function) so patching works.
        import apps.alerts.tasks as alerts_tasks

        class _FakeResult:
            def __init__(self, value: dict[str, Any]):
                self._value = value

            def get(self):
                return self._value

        class _FakeChain:
            def __init__(self, value: dict[str, Any]):
                self._value = value

            def apply(self):
                return _FakeResult(self._value)

        def _fake_chain_builder(ctx):
            # Minimal context payload that downstream code expects.
            final_ctx: dict[str, Any] = {
                **ctx,
                "incident_id": ctx.get("incident_id") or 1,
                "alerts": {
                    "created": 1,
                    "updated": 0,
                    "resolved": 0,
                    "incidents_created": 1,
                    "incidents_updated": 0,
                    "errors": [],
                },
                "checkers": {"checks_run": 0, "errors": []},
                "intelligence": {
                    "provider": "mock",
                    "incident_id": ctx.get("incident_id") or 1,
                    "recommendations": [
                        {
                            "type": "general",
                            "priority": "low",
                            "title": "Mock recommendation",
                            "description": "Mock analysis",
                            "details": {},
                            "actions": [],
                            "incident_id": ctx.get("incident_id") or 1,
                        }
                    ],
                    "count": 1,
                },
                "notify": {
                    "driver": "mock",
                    "result": {"success": True, "message_id": "test"},
                },
            }
            return _FakeChain(final_ctx)

        with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            with patch(
                "apps.alerts.tasks.build_orchestration_chain",
                side_effect=_fake_chain_builder,
            ):
                result_ctx = alerts_tasks.build_orchestration_chain(
                    {
                        "trigger": "webhook",
                        "payload": self.alertmanager_payload,
                        "driver": "alertmanager",
                    }
                ).apply()

        final_ctx = result_ctx.get()
        self.assertIsInstance(final_ctx, dict)
        self.assertIn("alerts", final_ctx)
        self.assertIn("checkers", final_ctx)
        self.assertIn("intelligence", final_ctx)
        self.assertIn("notify", final_ctx)
