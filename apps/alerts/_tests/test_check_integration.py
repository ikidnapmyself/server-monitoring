from unittest.mock import patch

from django.test import TestCase

from apps.alerts.check_integration import CheckAlertBridge
from apps.alerts.models import Alert, Incident
from apps.checkers.checkers import CheckResult, CheckStatus


class CheckAlertBridgeTests(TestCase):
    """Tests for the CheckAlertBridge integration."""

    def setUp(self):
        self.bridge = CheckAlertBridge(
            auto_create_incidents=True,
            hostname="test-server",
        )

    def test_check_result_to_parsed_alert_critical(self):
        """Test converting a critical check result to a parsed alert."""
        result = CheckResult(
            status=CheckStatus.CRITICAL,
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
        result = CheckResult(
            status=CheckStatus.OK,
            message="CPU usage normal at 25%",
            metrics={"cpu_percent": 25.0},
            checker_name="cpu",
        )

        parsed = self.bridge.check_result_to_parsed_alert(result)

        self.assertEqual(parsed.status, "resolved")
        self.assertEqual(parsed.severity, "info")

    def test_process_check_result_creates_alert(self):
        """Test that processing a check result creates an alert."""
        result = CheckResult(
            status=CheckStatus.WARNING,
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
        result = CheckResult(
            status=CheckStatus.WARNING,
            message="Disk usage at 80%",
            metrics={"disk_percent": 80.0},
            checker_name="disk",
        )

        processing_result = self.bridge.process_check_result(result)

        self.assertEqual(processing_result.incidents_created, 1)
        self.assertEqual(Incident.objects.count(), 1)

    def test_process_check_result_updates_existing_alert(self):
        """Test that subsequent checks update existing alert."""
        result = CheckResult(
            status=CheckStatus.WARNING,
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
        warning_result = CheckResult(
            status=CheckStatus.WARNING,
            message="CPU high",
            metrics={"cpu_percent": 85.0},
            checker_name="cpu",
        )
        self.bridge.process_check_result(warning_result)

        alert = Alert.objects.first()
        self.assertEqual(alert.status, "firing")

        # Resolve with OK check
        ok_result = CheckResult(
            status=CheckStatus.OK,
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
        from unittest.mock import MagicMock

        mock_checker_class = MagicMock()
        mock_checker_class.return_value.run.return_value = CheckResult(
            status=CheckStatus.CRITICAL,
            message="Test critical",
            metrics={"value": 99},
            checker_name="test",
        )

        with (
            patch.dict(
                "apps.alerts.check_integration.CHECKER_REGISTRY",
                {"test": mock_checker_class},
                clear=True,
            ),
            patch(
                "apps.alerts.check_integration.is_checker_enabled",
                return_value=True,
            ),
        ):
            check_result, processing_result = self.bridge.run_check_and_alert("test")

            self.assertEqual(check_result.status, CheckStatus.CRITICAL)
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
        bridge = CheckAlertBridge(
            auto_create_incidents=False,
            hostname="test-server",
        )

        result = CheckResult(
            status=CheckStatus.CRITICAL,
            message="Critical issue",
            metrics={},
            checker_name="cpu",
        )

        processing_result = bridge.process_check_result(result)

        self.assertEqual(processing_result.alerts_created, 1)
        self.assertEqual(processing_result.incidents_created, 0)
        self.assertEqual(Incident.objects.count(), 0)
