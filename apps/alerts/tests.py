"""
Tests for the alerts app.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.alerts.drivers import (
    AlertManagerDriver,
    GrafanaDriver,
    GenericWebhookDriver,
    detect_driver,
    get_driver,
)
from apps.alerts.drivers.datadog import DatadogDriver
from apps.alerts.drivers.newrelic import NewRelicDriver
from apps.alerts.drivers.pagerduty import PagerDutyDriver
from apps.alerts.drivers.zabbix import ZabbixDriver
from apps.alerts.models import Alert, AlertHistory, AlertStatus, Incident, IncidentStatus
from apps.alerts.services import AlertOrchestrator, IncidentManager, ProcessingResult


class AlertManagerDriverTests(TestCase):
    """Tests for AlertManager driver."""

    def setUp(self):
        self.driver = AlertManagerDriver()
        self.sample_payload = {
            "version": "4",
            "groupKey": "{}:{alertname=\"TestAlert\"}",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPU",
                        "severity": "critical",
                        "instance": "server1:9090",
                    },
                    "annotations": {
                        "summary": "High CPU usage detected",
                        "description": "CPU usage is above 90%",
                    },
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "abc123",
                }
            ],
            "groupLabels": {"alertname": "HighCPU"},
            "commonLabels": {"alertname": "HighCPU"},
            "commonAnnotations": {},
            "externalURL": "http://alertmanager:9093",
        }

    def test_validate_valid_payload(self):
        self.assertTrue(self.driver.validate(self.sample_payload))

    def test_validate_invalid_payload(self):
        self.assertFalse(self.driver.validate({"random": "data"}))

    def test_parse_payload(self):
        result = self.driver.parse(self.sample_payload)

        self.assertEqual(result.source, "alertmanager")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "HighCPU")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.fingerprint, "abc123")

    def test_parse_resolved_alert(self):
        self.sample_payload["alerts"][0]["status"] = "resolved"
        self.sample_payload["alerts"][0]["endsAt"] = "2024-01-08T11:00:00Z"

        result = self.driver.parse(self.sample_payload)
        alert = result.alerts[0]

        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.ended_at)


class GrafanaDriverTests(TestCase):
    """Tests for Grafana driver."""

    def setUp(self):
        self.driver = GrafanaDriver()
        self.sample_payload = {
            "receiver": "webhook",
            "status": "firing",
            "orgId": 1,
            "state": "alerting",
            "title": "Test Alert",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "DiskFull",
                        "severity": "warning",
                    },
                    "annotations": {
                        "summary": "Disk is almost full",
                    },
                    "startsAt": "2024-01-08T10:00:00Z",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
            "commonAnnotations": {},
            "externalURL": "http://grafana:3000",
        }

    def test_validate_valid_payload(self):
        self.assertTrue(self.driver.validate(self.sample_payload))

    def test_parse_unified_alerting(self):
        result = self.driver.parse(self.sample_payload)

        self.assertEqual(result.source, "grafana")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "DiskFull")
        self.assertEqual(alert.severity, "warning")


class GenericDriverTests(TestCase):
    """Tests for generic webhook driver."""

    def setUp(self):
        self.driver = GenericWebhookDriver()

    def test_parse_single_alert(self):
        payload = {
            "name": "Custom Alert",
            "status": "firing",
            "severity": "warning",
            "description": "Something happened",
        }

        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 1)
        self.assertEqual(result.alerts[0].name, "Custom Alert")

    def test_parse_multiple_alerts(self):
        payload = {
            "alerts": [
                {"name": "Alert 1", "status": "firing"},
                {"name": "Alert 2", "status": "resolved"},
            ]
        }

        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 2)

    def test_flexible_field_names(self):
        """Test that driver accepts various field name conventions."""
        payload = {
            "title": "My Alert",  # instead of "name"
            "state": "ok",  # instead of "status"
            "priority": "high",  # instead of "severity"
            "message": "Alert description",  # instead of "description"
        }

        result = self.driver.parse(payload)
        alert = result.alerts[0]

        self.assertEqual(alert.name, "My Alert")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")


class DriverDetectionTests(TestCase):
    """Tests for auto-detection of drivers."""

    def test_detect_alertmanager(self):
        payload = {
            "alerts": [],
            "status": "firing",
            "groupKey": "test",
            "receiver": "webhook",
            "groupLabels": {},
            "commonLabels": {},
        }

        driver = detect_driver(payload)
        self.assertIsInstance(driver, AlertManagerDriver)

    def test_detect_grafana(self):
        payload = {
            "alerts": [],
            "orgId": 1,
            "state": "alerting",
        }

        driver = detect_driver(payload)
        self.assertIsInstance(driver, GrafanaDriver)

    def test_fallback_to_generic(self):
        payload = {"name": "Custom Alert"}

        driver = detect_driver(payload)
        self.assertIsInstance(driver, GenericWebhookDriver)

    def test_get_driver_by_name(self):
        driver = get_driver("alertmanager")
        self.assertIsInstance(driver, AlertManagerDriver)

    def test_get_driver_invalid_name(self):
        with self.assertRaises(ValueError):
            get_driver("nonexistent")


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

        self.assertEqual(response.status_code, 200)
        data = response.json()
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

        self.assertEqual(response.status_code, 200)


class AlertModelTests(TestCase):
    """Tests for Alert model."""

    def test_is_firing(self):
        alert = Alert.objects.create(
            fingerprint="test",
            source="test",
            name="Test",
            status=AlertStatus.FIRING,
            started_at=timezone.now(),
        )

        self.assertTrue(alert.is_firing)

    def test_duration(self):
        start = timezone.now() - timedelta(hours=2)
        alert = Alert.objects.create(
            fingerprint="test",
            source="test",
            name="Test",
            status=AlertStatus.FIRING,
            started_at=start,
        )

        duration = alert.duration
        self.assertGreaterEqual(duration.total_seconds(), 7200)


class IncidentModelTests(TestCase):
    """Tests for Incident model."""

    def test_acknowledge_method(self):
        incident = Incident.objects.create(title="Test")
        incident.acknowledge()

        self.assertEqual(incident.status, IncidentStatus.ACKNOWLEDGED)

    def test_resolve_method(self):
        incident = Incident.objects.create(title="Test")
        incident.resolve(summary="Fixed")

        self.assertEqual(incident.status, IncidentStatus.RESOLVED)
        self.assertEqual(incident.summary, "Fixed")

    def test_is_open(self):
        incident = Incident.objects.create(title="Test", status=IncidentStatus.OPEN)
        self.assertTrue(incident.is_open)

        incident.status = IncidentStatus.RESOLVED
        self.assertFalse(incident.is_open)


class PagerDutyDriverTests(TestCase):
    """Tests for PagerDuty driver."""

    def setUp(self):
        self.driver = PagerDutyDriver()

    def test_validate_and_parse_v3_event(self):
        payload = {
            "event": {
                "id": "evt_1",
                "event_type": "incident.triggered",
                "resource_type": "incident",
                "occurred_at": "2024-01-08T10:00:00Z",
                "data": {
                    "id": "inc_1",
                    "type": "incident",
                    "status": "triggered",
                    "title": "PD: CPU high",
                    "urgency": "high",
                    "priority": {"name": "P1"},
                    "service": {"id": "svc_1", "summary": "API"},
                    "html_url": "https://example.pagerduty.test/incidents/inc_1",
                },
            }
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        self.assertEqual(parsed.source, "pagerduty")
        self.assertEqual(len(parsed.alerts), 1)

        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "inc_1")
        self.assertEqual(alert.name, "PD: CPU high")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["service_name"], "API")

    def test_parse_v3_resolved_from_event_type(self):
        payload = {
            "event": {
                "id": "evt_2",
                "event_type": "incident.resolved",
                "resource_type": "incident",
                "occurred_at": "2024-01-08T11:00:00Z",
                "data": {
                    "id": "inc_2",
                    "title": "PD: Disk full",
                    "urgency": "low",
                    "service": {"id": "svc_2", "summary": "DB"},
                },
            }
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "warning")


class NewRelicDriverTests(TestCase):
    """Tests for New Relic driver."""

    def setUp(self):
        self.driver = NewRelicDriver()

    def test_parse_classic_closed_is_resolved(self):
        payload = {
            "account_id": 123,
            "account_name": "Acme",
            "condition_id": 456,
            "condition_name": "NR: Apdex low",
            "current_state": "closed",
            "details": "SLO violated",
            "incident_id": 789,
            "incident_url": "https://example.newrelic.test/incidents/789",
            "severity": "CRITICAL",
            "timestamp": 1704708000,  # 2024-01-08
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "789")
        self.assertEqual(alert.name, "NR: Apdex low")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")

    def test_parse_workflow_high_priority_is_critical(self):
        payload = {
            "issueUrl": "https://example.newrelic.test/issues/ISSUE-1",
            "issueId": "ISSUE-1",
            "title": "NR workflow: error rate",
            "state": "open",
            "priority": "high",
            "createdAt": "2024-01-08T10:00:00Z",
            "accumulations": {"conditionName": ["NR: error rate"]},
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]
        self.assertEqual(alert.fingerprint, "ISSUE-1")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")


class DatadogDriverTests(TestCase):
    """Tests for Datadog driver."""

    def setUp(self):
        self.driver = DatadogDriver()

    def test_tags_parse_and_resolved_state(self):
        payload = {
            "alert_id": "123",
            "alert_title": "DD: latency",
            "alert_transition": "recovered",
            "alert_status": "ok",
            "alert_type": "error",
            "last_updated": "2024-01-08T10:00:00Z",
            "tags": "service:api,env:prod,flag",
            "url": "https://example.datadog.test/alerts/123",
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["service"], "api")
        self.assertEqual(alert.labels["env"], "prod")
        self.assertEqual(alert.labels["flag"], "true")

    def test_tags_parse_list_form(self):
        payload = {
            "alert_id": "456",
            "alert_title": "DD: memory",
            "alert_transition": "triggered",
            "alert_status": "warn",
            "alert_type": "metric_alert",
            "last_updated": "2024-01-08T10:00:00Z",
            "tags": ["service:worker", "env:staging", "canary"],
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.labels["service"], "worker")
        self.assertEqual(alert.labels["env"], "staging")
        self.assertEqual(alert.labels["canary"], "true")


class ZabbixDriverTests(TestCase):
    """Tests for Zabbix driver."""

    def setUp(self):
        self.driver = ZabbixDriver()

    def test_numeric_severity_and_event_value_resolved(self):
        payload = {
            "event_id": "1001",
            "trigger_id": "2002",
            "trigger_name": "ZBX: Disk full",
            "trigger_severity": "5",
            "event_value": "0",  # OK
            "host_name": "server-1",
            "event_date": "2024.01.08",
            "event_time": "10:00:00",
        }

        self.assertTrue(self.driver.validate(payload))

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.fingerprint, "1001")
        self.assertEqual(alert.status, "resolved")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.labels["host_name"], "server-1")

    def test_problem_status_is_firing(self):
        payload = {
            "event_id": "1002",
            "trigger_id": "2003",
            "trigger_name": "ZBX: CPU high",
            "trigger_severity": "2",
            "trigger_status": "PROBLEM",
            "host_name": "server-2",
        }

        parsed = self.driver.parse(payload)
        alert = parsed.alerts[0]

        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "warning")


class AlertOrchestratorEdgeCaseTests(TestCase):
    """Additional orchestrator tests for missing branches."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()

    def test_invalid_driver_type_is_reported_as_error(self):
        result = self.orchestrator.process_webhook({"anything": "goes"}, driver=object())
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


class WebhookViewPartialResponseTests(TestCase):
    """Tests for webhook partial responses when orchestrator reports errors."""

    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("alerts:webhook")

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
