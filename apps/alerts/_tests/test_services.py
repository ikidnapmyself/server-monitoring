from typing import Any, cast

from django.test import TestCase

from apps.alerts.models import Alert, AlertHistory, Incident, IncidentStatus
from apps.alerts.services import AlertOrchestrator, IncidentManager


class AlertOrchestratorTests(TestCase):
    """Tests for the AlertOrchestrator."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()
        self.alertmanager_payload = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "severity": "warning"},
                    "annotations": {"description": "Test description"},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "test123",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def test_process_creates_alert(self):
        result = self.orchestrator.process_webhook(self.alertmanager_payload)

        self.assertEqual(result.alerts_created, 1)
        self.assertEqual(Alert.objects.count(), 1)

        alert = Alert.objects.first()
        self.assertEqual(alert.name, "TestAlert")
        self.assertEqual(alert.status, "firing")

    def test_process_creates_incident(self):
        result = self.orchestrator.process_webhook(self.alertmanager_payload)

        self.assertEqual(result.incidents_created, 1)
        self.assertEqual(Incident.objects.count(), 1)

        incident = Incident.objects.first()
        self.assertEqual(incident.title, "TestAlert")
        self.assertEqual(incident.alerts.count(), 1)

    def test_process_updates_existing_alert(self):
        # Create initial alert
        self.orchestrator.process_webhook(self.alertmanager_payload)

        # Send same alert again
        result = self.orchestrator.process_webhook(self.alertmanager_payload)

        self.assertEqual(result.alerts_updated, 1)
        self.assertEqual(Alert.objects.count(), 1)

    def test_process_resolves_alert(self):
        # Create firing alert
        self.orchestrator.process_webhook(self.alertmanager_payload)

        # Send resolved alert
        self.alertmanager_payload["alerts"][0]["status"] = "resolved"
        self.alertmanager_payload["alerts"][0]["endsAt"] = "2024-01-08T11:00:00Z"

        result = self.orchestrator.process_webhook(self.alertmanager_payload)

        self.assertEqual(result.alerts_resolved, 1)

        alert = Alert.objects.first()
        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.ended_at)

    def test_process_records_history(self):
        self.orchestrator.process_webhook(self.alertmanager_payload)

        history = AlertHistory.objects.filter(event="created")
        self.assertEqual(history.count(), 1)

    def test_auto_resolve_incident(self):
        # Create and then resolve alert
        self.orchestrator.process_webhook(self.alertmanager_payload)

        self.alertmanager_payload["alerts"][0]["status"] = "resolved"
        self.orchestrator.process_webhook(self.alertmanager_payload)

        incident = Incident.objects.first()
        self.assertEqual(incident.status, IncidentStatus.RESOLVED)


class AlertOrchestratorEdgeCaseTests(TestCase):
    """Additional orchestrator tests for missing branches."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()

    def test_invalid_driver_type_is_reported_as_error(self):
        result = self.orchestrator.process_webhook(
            {"anything": "goes"},
            driver=cast(Any, object()),
        )
        self.assertTrue(result.has_errors)
        self.assertTrue(any("Invalid driver type" in e for e in result.errors))

    def test_attach_to_existing_incident_and_escalate_severity(self):
        payload_1 = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SameName", "severity": "warning"},
                    "annotations": {"description": "first"},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-1",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }
        payload_2 = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SameName", "severity": "critical"},
                    "annotations": {"description": "second"},
                    "startsAt": "2024-01-08T10:05:00Z",
                    "fingerprint": "fp-2",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

        r1 = self.orchestrator.process_webhook(payload_1)
        self.assertEqual(r1.incidents_created, 1)

        r2 = self.orchestrator.process_webhook(payload_2)
        self.assertEqual(r2.incidents_created, 0)
        self.assertEqual(r2.incidents_updated, 1)

        self.assertEqual(Incident.objects.count(), 1)
        incident = Incident.objects.first()
        self.assertEqual(incident.alerts.count(), 2)
        self.assertEqual(incident.severity, "critical")


class IncidentManagerTests(TestCase):
    """Tests for the IncidentManager."""

    def setUp(self):
        self.incident = Incident.objects.create(
            title="Test Incident",
            severity="warning",
        )

    def test_acknowledge(self):
        incident = IncidentManager.acknowledge(self.incident.pk)

        self.assertEqual(incident.status, IncidentStatus.ACKNOWLEDGED)
        self.assertIsNotNone(incident.acknowledged_at)

    def test_resolve(self):
        incident = IncidentManager.resolve(
            self.incident.pk,
            summary="Fixed the issue",
        )

        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
        self.assertEqual(incident.summary, "Fixed the issue")
        self.assertIsNotNone(incident.resolved_at)

    def test_close(self):
        self.incident.status = IncidentStatus.RESOLVED
        self.incident.save()

        incident = IncidentManager.close(self.incident.pk)

        self.assertEqual(incident.status, IncidentStatus.CLOSED)
        self.assertIsNotNone(incident.closed_at)

    def test_add_note(self):
        incident = IncidentManager.add_note(
            self.incident.pk,
            note="Investigation in progress",
            author="admin",
        )

        self.assertIn("notes", incident.metadata)
        self.assertEqual(len(incident.metadata["notes"]), 1)
        self.assertEqual(incident.metadata["notes"][0]["text"], "Investigation in progress")
