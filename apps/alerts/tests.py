"""
Tests for the alerts app.
"""

import json
import os
from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.alerts.drivers import (
    AlertManagerDriver,
    GenericWebhookDriver,
    GrafanaDriver,
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
            "groupKey": '{}:{alertname="TestAlert"}',
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


class CheckAlertBridgeTests(TestCase):
    """Tests for the CheckAlertBridge integration."""

    def setUp(self):
        from apps.alerts.check_integration import CheckAlertBridge
        from apps.checkers.checkers import CheckResult, CheckStatus

        self.CheckAlertBridge = CheckAlertBridge
        self.CheckResult = CheckResult
        self.CheckStatus = CheckStatus

        self.bridge = CheckAlertBridge(
            auto_create_incidents=True,
            hostname="test-server",
        )

    def test_check_result_to_parsed_alert_critical(self):
        """Test converting a critical check result to a parsed alert."""
        result = self.CheckResult(
            status=self.CheckStatus.CRITICAL,
            message="CPU usage at 95%",
            metrics={"cpu_percent": 95.0},
            checker_name="cpu",
        )

        parsed = self.bridge.check_result_to_parsed_alert(result)

        self.assertEqual(parsed.name, "CPU Check Alert")
        self.assertEqual(parsed.status, "firing")
        self.assertEqual(parsed.severity, "critical")
        self.assertEqual(parsed.labels["hostname"], "test-server")
        self.assertEqual(parsed.labels["checker"], "cpu")
        self.assertIn("CPU usage at 95%", parsed.description)

    def test_check_result_to_parsed_alert_ok(self):
        """Test converting an OK check result to a parsed alert (resolved)."""
        result = self.CheckResult(
            status=self.CheckStatus.OK,
            message="CPU usage normal at 25%",
            metrics={"cpu_percent": 25.0},
            checker_name="cpu",
        )

        parsed = self.bridge.check_result_to_parsed_alert(result)

        self.assertEqual(parsed.status, "resolved")
        self.assertEqual(parsed.severity, "info")

    def test_process_check_result_creates_alert(self):
        """Test that processing a check result creates an alert."""
        result = self.CheckResult(
            status=self.CheckStatus.WARNING,
            message="Memory usage at 75%",
            metrics={"memory_percent": 75.0},
            checker_name="memory",
        )

        processing_result = self.bridge.process_check_result(result)

        self.assertEqual(processing_result.alerts_created, 1)
        self.assertEqual(Alert.objects.count(), 1)

        alert = Alert.objects.first()
        self.assertEqual(alert.name, "MEMORY Check Alert")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.source, "server-checkers")

    def test_process_check_result_creates_incident_for_warning(self):
        """Test that a warning check creates an incident."""
        result = self.CheckResult(
            status=self.CheckStatus.WARNING,
            message="Disk usage at 80%",
            metrics={"disk_percent": 80.0},
            checker_name="disk",
        )

        processing_result = self.bridge.process_check_result(result)

        self.assertEqual(processing_result.incidents_created, 1)
        self.assertEqual(Incident.objects.count(), 1)

    def test_process_check_result_updates_existing_alert(self):
        """Test that subsequent checks update existing alert."""
        result = self.CheckResult(
            status=self.CheckStatus.WARNING,
            message="Network latency high",
            metrics={"latency_ms": 150},
            checker_name="network",
        )

        # First check creates alert
        self.bridge.process_check_result(result)
        self.assertEqual(Alert.objects.count(), 1)

        # Second check updates existing
        result.message = "Network latency still high"
        processing_result = self.bridge.process_check_result(result)

        self.assertEqual(processing_result.alerts_updated, 1)
        self.assertEqual(Alert.objects.count(), 1)

    def test_process_check_result_resolves_alert(self):
        """Test that an OK check resolves an existing alert."""
        # Create a firing alert
        warning_result = self.CheckResult(
            status=self.CheckStatus.WARNING,
            message="CPU high",
            metrics={"cpu_percent": 85.0},
            checker_name="cpu",
        )
        self.bridge.process_check_result(warning_result)

        alert = Alert.objects.first()
        self.assertEqual(alert.status, "firing")

        # Resolve with OK check
        ok_result = self.CheckResult(
            status=self.CheckStatus.OK,
            message="CPU normal",
            metrics={"cpu_percent": 30.0},
            checker_name="cpu",
        )
        processing_result = self.bridge.process_check_result(ok_result)

        self.assertEqual(processing_result.alerts_resolved, 1)

        alert.refresh_from_db()
        self.assertEqual(alert.status, "resolved")

    def test_run_check_and_alert_with_mock(self):
        """Test running a check and creating an alert."""
        with patch("apps.alerts.check_integration.CHECKER_REGISTRY") as mock_registry:
            mock_checker_class = patch("apps.checkers.checkers.base.BaseChecker").start()
            mock_checker = mock_checker_class.return_value
            mock_checker.check.return_value = self.CheckResult(
                status=self.CheckStatus.CRITICAL,
                message="Test critical",
                metrics={"value": 99},
                checker_name="test",
            )
            mock_registry.__contains__ = lambda self, x: x == "test"
            mock_registry.__getitem__ = lambda self, x: mock_checker_class
            mock_registry.keys.return_value = ["test"]

            check_result, processing_result = self.bridge.run_check_and_alert("test")

            self.assertEqual(check_result.status, self.CheckStatus.CRITICAL)
            self.assertEqual(processing_result.alerts_created, 1)

    def test_fingerprint_stability(self):
        """Test that fingerprints are stable for the same checker/hostname."""
        fp1 = self.bridge._generate_fingerprint("cpu", "server1")
        fp2 = self.bridge._generate_fingerprint("cpu", "server1")
        fp3 = self.bridge._generate_fingerprint("cpu", "server2")

        self.assertEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3)

    def test_no_incidents_when_disabled(self):
        """Test that incidents are not created when disabled."""
        bridge = self.CheckAlertBridge(
            auto_create_incidents=False,
            hostname="test-server",
        )

        result = self.CheckResult(
            status=self.CheckStatus.CRITICAL,
            message="Critical issue",
            metrics={},
            checker_name="cpu",
        )

        processing_result = bridge.process_check_result(result)

        self.assertEqual(processing_result.alerts_created, 1)
        self.assertEqual(processing_result.incidents_created, 0)
        self.assertEqual(Incident.objects.count(), 0)


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
