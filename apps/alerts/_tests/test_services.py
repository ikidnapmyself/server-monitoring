from typing import Any, cast

from django.test import TestCase
from django.utils import timezone

from apps.alerts.drivers.base import BaseAlertDriver, ParsedAlert, ParsedPayload
from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus
from apps.alerts.services import (
    AlertOrchestrator,
    AlertQueryService,
    IncidentManager,
    ProcessingResult,
)


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

    def test_created_alert_carries_trace_id(self):
        """A webhook-ingested alert is stamped with the orchestrator's trace_id."""
        AlertOrchestrator(trace_id="trace-xyz").process_webhook(self.alertmanager_payload)
        self.assertEqual(Alert.objects.get().trace_id, "trace-xyz")

    def test_default_trace_id_is_blank(self):
        self.orchestrator.process_webhook(self.alertmanager_payload)
        self.assertEqual(Alert.objects.get().trace_id, "")

    def test_alert_links_existing_node_from_instance_label(self):
        from apps.alerts.models import Node

        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        payload = dict(self.alertmanager_payload)
        payload["alerts"] = [
            {
                "status": "firing",
                "labels": {
                    "alertname": "TestAlert",
                    "severity": "warning",
                    "instance_id": "web-03",
                },
                "annotations": {},
                "startsAt": "2024-01-08T10:00:00Z",
                "fingerprint": "fp-node",
            }
        ]
        self.orchestrator.process_webhook(payload)
        self.assertEqual(Alert.objects.get().node_id, node.id)

    def test_alert_without_instance_label_has_no_node(self):
        self.orchestrator.process_webhook(self.alertmanager_payload)
        self.assertIsNone(Alert.objects.get().node_id)

    def test_alert_with_unknown_instance_has_no_node(self):
        payload = dict(self.alertmanager_payload)
        payload["alerts"] = [
            {
                "status": "firing",
                "labels": {"alertname": "A", "instance_id": "ghost"},
                "annotations": {},
                "startsAt": "2024-01-08T10:00:00Z",
                "fingerprint": "fp-ghost",
            }
        ]
        self.orchestrator.process_webhook(payload)
        self.assertIsNone(Alert.objects.get().node_id)

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

    def test_configured_node_alert_persisted_with_reevaluated_severity(self):
        from apps.alerts.models import Node

        Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        payload = {
            "source": "cluster",
            "instance_id": "web-03",
            "hostname": "h",
            "alerts": [
                {
                    "fingerprint": "cpu-web-03",
                    "name": "cpu: high",
                    "status": "firing",
                    "severity": "critical",
                    "labels": {"checker": "cpu"},
                    "metrics": {"cpu_percent": 95.2},
                }
            ],
        }
        self.orchestrator.process_webhook(payload, driver="cluster")
        alert = Alert.objects.get(fingerprint="cpu-web-03")
        self.assertEqual(alert.severity, "info")
        self.assertEqual(alert.status, "resolved")
        self.assertIn("severity_reevaluated", alert.annotations)


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

    def test_acknowledge_with_acknowledged_by(self):
        incident = IncidentManager.acknowledge(self.incident.pk, acknowledged_by="oncall")

        self.assertEqual(incident.status, IncidentStatus.ACKNOWLEDGED)
        self.assertEqual(incident.metadata["acknowledged_by"], "oncall")

    def test_resolve_with_resolved_by(self):
        incident = IncidentManager.resolve(self.incident.pk, summary="Done", resolved_by="admin")

        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
        self.assertEqual(incident.metadata["resolved_by"], "admin")

    def test_add_note_appends_to_existing_notes(self):
        self.incident.metadata = {"notes": [{"text": "first", "author": "", "timestamp": "t1"}]}
        self.incident.save(update_fields=["metadata"])

        incident = IncidentManager.add_note(self.incident.pk, note="second", author="admin")

        self.assertEqual(len(incident.metadata["notes"]), 2)
        self.assertEqual(incident.metadata["notes"][1]["text"], "second")

    def test_get_open_incidents(self):
        Incident.objects.create(title="Open", severity="warning", status=IncidentStatus.OPEN)
        Incident.objects.create(
            title="Acked", severity="warning", status=IncidentStatus.ACKNOWLEDGED
        )
        Incident.objects.create(title="Closed", severity="warning", status=IncidentStatus.CLOSED)

        open_incidents = IncidentManager.get_open_incidents()
        # setUp creates one OPEN incident + the two above (Open, Acked)
        titles = set(open_incidents.values_list("title", flat=True))
        self.assertIn("Open", titles)
        self.assertIn("Acked", titles)
        self.assertNotIn("Closed", titles)

    def test_get_incident_with_alerts(self):
        alert = Alert.objects.create(
            fingerprint="fp",
            source="test",
            name="Alert",
            severity="warning",
            status=AlertStatus.FIRING,
            started_at=timezone.now(),
            incident=self.incident,
        )
        AlertHistory.objects.create(alert=alert, event="created", new_status="firing")

        incident = IncidentManager.get_incident_with_alerts(self.incident.pk)
        self.assertEqual(incident.pk, self.incident.pk)
        self.assertEqual(incident.alerts.count(), 1)


class ProcessingResultTests(TestCase):
    """Tests for the ProcessingResult dataclass."""

    def test_total_processed(self):
        result = ProcessingResult(alerts_created=2, alerts_updated=3, alerts_resolved=1)
        self.assertEqual(result.total_processed, 6)

    def test_has_errors_false(self):
        self.assertFalse(ProcessingResult().has_errors)

    def test_has_errors_true(self):
        self.assertTrue(ProcessingResult(errors=["oops"]).has_errors)


class AlertOrchestratorDriverTests(TestCase):
    """Tests for driver handling in AlertOrchestrator."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()

    def test_undetectable_payload_returns_error(self):
        result = self.orchestrator.process_webhook({"unknown": "format"})
        self.assertTrue(result.has_errors)
        self.assertIn("Could not detect driver for payload", result.errors[0])

    def test_string_driver_name(self):
        result = self.orchestrator.process_webhook(
            {
                "version": "4",
                "groupKey": "test",
                "receiver": "webhook",
                "status": "firing",
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {"alertname": "Test", "severity": "warning"},
                        "annotations": {},
                        "startsAt": "2024-01-08T10:00:00Z",
                        "fingerprint": "fp-str",
                    }
                ],
                "groupLabels": {},
                "commonLabels": {},
            },
            driver="alertmanager",
        )
        self.assertEqual(result.alerts_created, 1)

    def test_driver_instance(self):
        class FakeDriver(BaseAlertDriver):
            name = "fake"

            def validate(self, payload):
                return True

            def parse(self, payload):
                return ParsedPayload(
                    alerts=[
                        ParsedAlert(
                            fingerprint="fp-inst",
                            name="FakeAlert",
                            status="firing",
                            severity="warning",
                            description="test",
                            labels={},
                            annotations={},
                            started_at=timezone.now(),
                            raw_payload=payload,
                        )
                    ],
                    source="fake-source",
                )

        result = self.orchestrator.process_webhook({"any": "payload"}, driver=FakeDriver())
        self.assertEqual(result.alerts_created, 1)

    def test_auto_resolve_disabled_skips_resolution(self):
        orchestrator = AlertOrchestrator(auto_resolve_incidents=False)
        payload = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "Test", "severity": "warning"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-noresolve",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }
        orchestrator.process_webhook(payload)

        # Resolve alert
        payload["alerts"][0]["status"] = "resolved"
        orchestrator.process_webhook(payload)

        # Incident should stay open
        incident = Incident.objects.first()
        self.assertEqual(incident.status, IncidentStatus.OPEN)

    def test_auto_create_incidents_disabled(self):
        orchestrator = AlertOrchestrator(auto_create_incidents=False)
        payload = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "NoIncident", "severity": "critical"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-noinc",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }
        result = orchestrator.process_webhook(payload)

        self.assertEqual(result.alerts_created, 1)
        self.assertEqual(result.incidents_created, 0)
        self.assertEqual(Incident.objects.count(), 0)

    def test_refired_alert(self):
        """Test alert going from resolved back to firing."""
        orchestrator = AlertOrchestrator()
        payload = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "Refire", "severity": "warning"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-refire",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

        # Fire
        orchestrator.process_webhook(payload)
        # Resolve
        payload["alerts"][0]["status"] = "resolved"
        orchestrator.process_webhook(payload)

        alert = Alert.objects.first()
        self.assertEqual(alert.status, "resolved")

        # Refire
        payload["alerts"][0]["status"] = "firing"
        result = orchestrator.process_webhook(payload)

        # Should count as a status change, not alerts_updated
        self.assertEqual(result.alerts_updated, 0)

        alert.refresh_from_db()
        self.assertEqual(alert.status, "firing")
        self.assertIsNone(alert.ended_at)

        # History should have a "refired" event
        self.assertTrue(AlertHistory.objects.filter(event="refired").exists())

    def test_attach_without_severity_upgrade(self):
        """Test attaching to incident when new alert has lower severity."""
        orchestrator = AlertOrchestrator()
        payload_critical = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SharedName", "severity": "critical"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "fp-crit",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }
        payload_warning = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "SharedName", "severity": "warning"},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:05:00Z",
                    "fingerprint": "fp-warn",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

        orchestrator.process_webhook(payload_critical)
        result = orchestrator.process_webhook(payload_warning)

        self.assertEqual(result.incidents_updated, 1)

        incident = Incident.objects.first()
        self.assertEqual(incident.severity, "critical")  # unchanged


class AlertQueryServiceTests(TestCase):
    """Tests for the AlertQueryService."""

    def setUp(self):
        self.now = timezone.now()
        self.alert = Alert.objects.create(
            fingerprint="fp-query",
            source="test-source",
            name="QueryAlert",
            severity="warning",
            status=AlertStatus.FIRING,
            started_at=self.now,
        )

    def test_get_firing_alerts(self):
        alerts = AlertQueryService.get_firing_alerts()
        self.assertEqual(alerts.count(), 1)
        self.assertEqual(alerts.first().pk, self.alert.pk)

    def test_get_alerts_by_severity(self):
        alerts = AlertQueryService.get_alerts_by_severity("warning")
        self.assertEqual(alerts.count(), 1)

        alerts = AlertQueryService.get_alerts_by_severity("critical")
        self.assertEqual(alerts.count(), 0)

    def test_get_alerts_by_source(self):
        alerts = AlertQueryService.get_alerts_by_source("test-source")
        self.assertEqual(alerts.count(), 1)

        alerts = AlertQueryService.get_alerts_by_source("other")
        self.assertEqual(alerts.count(), 0)

    def test_get_recent_alerts(self):
        alerts = AlertQueryService.get_recent_alerts(hours=24)
        self.assertEqual(alerts.count(), 1)

    def test_get_alert_with_history(self):
        AlertHistory.objects.create(alert=self.alert, event="created", new_status="firing")

        alert = AlertQueryService.get_alert_with_history(self.alert.pk)
        self.assertEqual(alert.pk, self.alert.pk)
        self.assertEqual(alert.history.count(), 1)


class IncidentGroupingTests(TestCase):
    """Incident grouping is scoped by (name, instance) — no cross-host merge."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()

    def _am(self, fingerprint, instance, name="HighCPU", severity="warning"):
        return {
            "version": "4",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": name, "severity": severity, "instance": instance},
                    "annotations": {},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": fingerprint,
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def test_same_name_different_instance_do_not_merge(self):
        # THE BUG: two hosts firing the same alert must be two incidents.
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        self.orchestrator.process_webhook(self._am("fp2", "web-04"), driver="alertmanager")
        self.assertEqual(Incident.objects.count(), 2)

    def test_same_name_same_instance_merge(self):
        self.orchestrator.process_webhook(self._am("fp1", "web-03"), driver="alertmanager")
        self.orchestrator.process_webhook(self._am("fp2", "web-03"), driver="alertmanager")
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(Incident.objects.first().alerts.count(), 2)

    def test_different_name_same_instance_do_not_merge(self):
        self.orchestrator.process_webhook(
            self._am("fp1", "web-03", name="HighCPU"), driver="alertmanager"
        )
        self.orchestrator.process_webhook(
            self._am("fp2", "web-03", name="DiskLow"), driver="alertmanager"
        )
        self.assertEqual(Incident.objects.count(), 2)

    def test_attach_escalates_severity(self):
        self.orchestrator.process_webhook(
            self._am("fp1", "web-03", severity="warning"), driver="alertmanager"
        )
        self.orchestrator.process_webhook(
            self._am("fp2", "web-03", severity="critical"), driver="alertmanager"
        )
        inc = Incident.objects.get()
        self.assertEqual(inc.severity, "critical")

    def test_no_instance_label_groups_by_name_only(self):
        # Alerts with no instance/hostname label fall back to "" — grouped by name.
        self.orchestrator.process_webhook(self._am("g1", "", name="Generic"), driver="alertmanager")
        self.orchestrator.process_webhook(self._am("g2", "", name="Generic"), driver="alertmanager")
        self.assertEqual(Incident.objects.count(), 1)


class InstanceKeyFromLabelsTests(TestCase):
    """Tests for the shared, malformed-input-safe label -> instance key helper."""

    def test_prefers_instance_id_then_instance_then_hostname(self):
        from apps.alerts.services import instance_key_from_labels

        self.assertEqual(
            instance_key_from_labels({"instance_id": "a", "instance": "b", "hostname": "c"}),
            "a",
        )
        self.assertEqual(instance_key_from_labels({"instance": "b", "hostname": "c"}), "b")
        self.assertEqual(instance_key_from_labels({"hostname": "c"}), "c")

    def test_empty_or_none_labels_return_empty_string(self):
        from apps.alerts.services import instance_key_from_labels

        self.assertEqual(instance_key_from_labels({}), "")
        self.assertEqual(instance_key_from_labels(None), "")

    def test_non_dict_labels_do_not_raise(self):
        from apps.alerts.services import instance_key_from_labels

        self.assertEqual(instance_key_from_labels("pwned"), "")
        self.assertEqual(instance_key_from_labels(["not", "a", "dict"]), "")

    def test_incident_instance_key_delegates(self):
        from apps.alerts.services import incident_instance_key

        class _Alert:
            labels = {"instance": "web-1"}

        self.assertEqual(incident_instance_key(_Alert()), "web-1")


class DiffAlertTests(TestCase):
    """Tests for AlertOrchestrator._diff_alert."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()
        self.alert = Alert.objects.create(
            fingerprint="fp",
            source="alertmanager",
            name="A",
            severity="warning",
            status="firing",
            description="old desc",
            labels={"a": "1"},
            annotations={"x": "1"},
            started_at=timezone.now(),
        )

    def _parsed(self, **overrides):
        base = dict(
            fingerprint="fp",
            name="A",
            status="firing",
            started_at=timezone.now(),
            severity="warning",
            description="old desc",
            labels={"a": "1"},
            annotations={"x": "1"},
        )
        base.update(overrides)
        return ParsedAlert(**base)

    def test_no_changes_returns_empty(self):
        self.assertEqual(self.orchestrator._diff_alert(self.alert, self._parsed()), {})

    def test_severity_change_captured(self):
        diff = self.orchestrator._diff_alert(self.alert, self._parsed(severity="critical"))
        self.assertEqual(diff, {"severity": ["warning", "critical"]})

    def test_description_and_annotation_change_captured(self):
        diff = self.orchestrator._diff_alert(
            self.alert, self._parsed(description="new desc", annotations={"x": "2"})
        )
        self.assertEqual(
            diff,
            {"description": ["old desc", "new desc"], "annotations": [{"x": "1"}, {"x": "2"}]},
        )

    def test_raw_payload_and_name_not_diffed(self):
        diff = self.orchestrator._diff_alert(
            self.alert, self._parsed(name="B", raw_payload={"huge": "churn"})
        )
        self.assertEqual(diff, {})
