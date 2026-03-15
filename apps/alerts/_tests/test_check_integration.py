from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from apps.alerts.check_integration import CheckAlertBridge, CheckAlertResult
from apps.alerts.models import Alert, AlertStatus, Incident, IncidentStatus
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

    def test_run_checks_and_alert_defaults_to_all_checkers(self):
        """Test that run_checks_and_alert uses all registry checkers when none specified."""

        mock_checker_class = MagicMock()
        mock_checker_class.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="All good",
            metrics={"value": 10},
            checker_name="fake",
        )

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"fake_a": mock_checker_class, "fake_b": mock_checker_class},
            clear=True,
        ):
            result = self.bridge.run_checks_and_alert()

        self.assertEqual(result.checks_run, 2)

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

    def test_check_alert_result_has_errors(self):
        """Test CheckAlertResult.has_errors returns True when errors exist."""
        result = CheckAlertResult(errors=["something broke"])
        self.assertTrue(result.has_errors)

        empty = CheckAlertResult()
        self.assertFalse(empty.has_errors)

    def test_extra_labels_merged_into_parsed_alert(self):
        """Test that extra labels are merged into the parsed alert."""
        result = CheckResult(
            status=CheckStatus.WARNING,
            message="test",
            metrics={},
            checker_name="cpu",
        )

        parsed = self.bridge.check_result_to_parsed_alert(
            result, labels={"env": "prod", "region": "us-east"}
        )

        self.assertEqual(parsed.labels["env"], "prod")
        self.assertEqual(parsed.labels["region"], "us-east")
        self.assertEqual(parsed.labels["checker"], "cpu")

    def test_non_primitive_metric_values_excluded_from_labels(self):
        """Test that metric values that aren't str/int/float/bool are skipped."""
        result = CheckResult(
            status=CheckStatus.OK,
            message="test",
            metrics={
                "simple": 42,
                "nested": {"a": 1},
                "listed": [1, 2, 3],
            },
            checker_name="cpu",
        )

        parsed = self.bridge.check_result_to_parsed_alert(result)

        self.assertEqual(parsed.labels["metric_simple"], "42")
        self.assertNotIn("metric_nested", parsed.labels)
        self.assertNotIn("metric_listed", parsed.labels)

    def test_error_appended_to_description(self):
        """Test that result.error is appended to the alert description."""
        result = CheckResult(
            status=CheckStatus.CRITICAL,
            message="Check failed",
            metrics={},
            checker_name="cpu",
            error="Connection refused",
        )

        parsed = self.bridge.check_result_to_parsed_alert(result)

        self.assertIn("Check failed", parsed.description)
        self.assertIn("Error: Connection refused", parsed.description)

    def test_process_parsed_payload_catches_exceptions(self):
        """Test that exceptions during processing are caught and added to errors."""
        result = CheckResult(
            status=CheckStatus.WARNING,
            message="test",
            metrics={},
            checker_name="cpu",
        )

        with patch.object(self.bridge, "_process_alert", side_effect=Exception("db error")):
            processing_result = self.bridge.process_check_result(result)

        self.assertTrue(processing_result.has_errors)
        self.assertIn("db error", processing_result.errors[0])

    def test_already_resolved_alert_no_change(self):
        """Test that a resolved check for an already-resolved alert is a no-op."""
        # Create and resolve an alert
        warning = CheckResult(
            status=CheckStatus.WARNING,
            message="issue",
            metrics={},
            checker_name="cpu",
        )
        self.bridge.process_check_result(warning)

        ok = CheckResult(
            status=CheckStatus.OK,
            message="fixed",
            metrics={},
            checker_name="cpu",
        )
        self.bridge.process_check_result(ok)

        alert = Alert.objects.first()
        self.assertEqual(alert.status, AlertStatus.RESOLVED)

        # Send another resolved — should be a no-op
        result = self.bridge.process_check_result(ok)
        self.assertEqual(result.alerts_created, 0)
        self.assertEqual(result.alerts_updated, 0)
        self.assertEqual(result.alerts_resolved, 0)

    def test_resolved_alert_for_unknown_fingerprint_skipped(self):
        """Test that a resolved alert with no existing record is skipped."""
        ok = CheckResult(
            status=CheckStatus.OK,
            message="all good",
            metrics={},
            checker_name="cpu",
        )
        result = self.bridge.process_check_result(ok)

        self.assertEqual(result.alerts_created, 0)
        self.assertEqual(result.alerts_resolved, 0)
        self.assertEqual(Alert.objects.count(), 0)

    def test_update_alert_same_severity_no_history(self):
        """Test that updating an alert with same severity doesn't create history."""
        from apps.alerts.models import AlertHistory

        result = CheckResult(
            status=CheckStatus.WARNING,
            message="latency high",
            metrics={"ms": 100},
            checker_name="network",
        )
        self.bridge.process_check_result(result)

        # Update with same severity
        result.message = "latency still high"
        self.bridge.process_check_result(result)

        # No new severity_changed history entry
        severity_changes = AlertHistory.objects.filter(event="severity_changed").count()
        self.assertEqual(severity_changes, 0)

    def test_existing_incident_attach_and_severity_upgrade(self):
        """Test attaching to existing incident and upgrading severity."""
        # Manually create an open incident with a firing alert (different source)
        # so the bridge's fingerprint lookup won't find it but incident query will.
        incident = Incident.objects.create(
            title="Disk issue",
            severity="warning",
            status=IncidentStatus.OPEN,
        )
        Alert.objects.create(
            fingerprint="webhook-fp",
            source="webhook",
            name="Disk Alert",
            severity="warning",
            status=AlertStatus.FIRING,
            labels={"checker": "disk", "hostname": "test-server"},
            annotations={},
            raw_payload={},
            started_at=timezone.now(),
            incident=incident,
        )

        # Bridge creates a critical disk alert — different fingerprint/source,
        # so it creates a new alert. _create_or_attach_incident finds the
        # existing open incident and upgrades severity.
        critical = CheckResult(
            status=CheckStatus.CRITICAL,
            message="disk full",
            metrics={},
            checker_name="disk",
        )
        result = self.bridge.process_check_result(critical)

        incident.refresh_from_db()
        self.assertEqual(incident.severity, "critical")
        self.assertEqual(result.incidents_updated, 1)

    def test_run_check_and_alert_unknown_checker_raises(self):
        """Test that an unknown checker name raises ValueError."""
        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {},
            clear=True,
        ):
            with self.assertRaises(ValueError) as ctx:
                self.bridge.run_check_and_alert("nonexistent")

            self.assertIn("Unknown checker: nonexistent", str(ctx.exception))

    def test_run_checks_and_alert_catches_checker_exception(self):
        """Test that exceptions from individual checkers are caught in batch."""
        mock_ok = MagicMock()
        mock_ok.return_value.run.return_value = CheckResult(
            status=CheckStatus.OK,
            message="ok",
            metrics={},
            checker_name="good",
        )

        mock_bad = MagicMock()
        mock_bad.return_value.run.side_effect = RuntimeError("checker crashed")

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"good": mock_ok, "bad": mock_bad},
            clear=True,
        ):
            result = self.bridge.run_checks_and_alert(["good", "bad"])

        self.assertEqual(result.checks_run, 1)
        self.assertTrue(result.has_errors)
        self.assertIn("bad: checker crashed", result.errors[0])

    def test_run_checks_and_alert_accumulates_processing_errors(self):
        """Test that processing errors from individual checks are accumulated."""
        mock_checker = MagicMock()
        mock_checker.return_value.run.return_value = CheckResult(
            status=CheckStatus.WARNING,
            message="test",
            metrics={},
            checker_name="test",
        )

        with patch.dict(
            "apps.alerts.check_integration.CHECKER_REGISTRY",
            {"test": mock_checker},
            clear=True,
        ):
            with patch.object(
                self.bridge,
                "process_check_result",
                return_value=MagicMock(
                    alerts_created=1,
                    alerts_updated=0,
                    alerts_resolved=0,
                    incidents_created=0,
                    incidents_updated=0,
                    has_errors=True,
                    errors=["processing failed"],
                ),
            ):
                result = self.bridge.run_checks_and_alert(["test"])

        self.assertEqual(result.checks_run, 1)
        self.assertIn("processing failed", result.errors)

    def test_auto_resolve_incidents_disabled_skips_resolution_check(self):
        """Test that incident resolution is skipped when auto_resolve is False."""
        bridge = CheckAlertBridge(
            auto_create_incidents=True,
            auto_resolve_incidents=False,
            hostname="test-server",
        )

        # Create a warning alert (creates incident)
        warning = CheckResult(
            status=CheckStatus.WARNING,
            message="issue",
            metrics={},
            checker_name="cpu",
        )
        bridge.process_check_result(warning)
        self.assertEqual(Incident.objects.count(), 1)

        # Resolve the alert
        ok = CheckResult(
            status=CheckStatus.OK,
            message="ok",
            metrics={},
            checker_name="cpu",
        )
        bridge.process_check_result(ok)

        # Incident should NOT be auto-resolved
        incident = Incident.objects.first()
        self.assertEqual(incident.status, IncidentStatus.OPEN)

    def test_update_alert_with_changed_severity_creates_history(self):
        """Test that updating an alert with different severity creates history."""
        from apps.alerts.models import AlertHistory

        # Create a warning alert
        warning = CheckResult(
            status=CheckStatus.WARNING,
            message="issue",
            metrics={},
            checker_name="cpu",
        )
        self.bridge.process_check_result(warning)

        # Update to critical severity
        critical = CheckResult(
            status=CheckStatus.CRITICAL,
            message="worse now",
            metrics={},
            checker_name="cpu",
        )
        self.bridge.process_check_result(critical)

        severity_changes = AlertHistory.objects.filter(event="severity_changed").count()
        self.assertEqual(severity_changes, 1)

    def test_existing_incident_attach_without_severity_upgrade(self):
        """Test attaching to existing incident without upgrading severity."""
        # Create an open incident with a critical alert
        incident = Incident.objects.create(
            title="Critical issue",
            severity="critical",
            status=IncidentStatus.OPEN,
        )
        Alert.objects.create(
            fingerprint="webhook-fp",
            source="webhook",
            name="CPU Alert",
            severity="critical",
            status=AlertStatus.FIRING,
            labels={"checker": "cpu", "hostname": "test-server"},
            annotations={},
            raw_payload={},
            started_at=timezone.now(),
            incident=incident,
        )

        # Bridge creates a warning alert for same checker — attaches but no upgrade
        warning = CheckResult(
            status=CheckStatus.WARNING,
            message="cpu warm",
            metrics={},
            checker_name="cpu",
        )
        result = self.bridge.process_check_result(warning)

        incident.refresh_from_db()
        self.assertEqual(incident.severity, "critical")  # unchanged
        self.assertEqual(result.incidents_updated, 1)
