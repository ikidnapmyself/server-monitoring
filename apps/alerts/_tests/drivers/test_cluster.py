from django.test import TestCase, override_settings

from apps.alerts.drivers.cluster import ClusterDriver


class ClusterDriverValidateTests(TestCase):
    """Tests for ClusterDriver.validate()."""

    def setUp(self):
        self.driver = ClusterDriver()

    def test_validate_accepts_cluster_payload(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "CPU high", "status": "firing"}],
        }
        self.assertTrue(self.driver.validate(payload))

    def test_validate_rejects_missing_source(self):
        payload = {"instance_id": "web-01", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_wrong_source(self):
        payload = {"source": "grafana", "instance_id": "web-01", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_missing_instance_id(self):
        payload = {"source": "cluster", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_missing_alerts(self):
        payload = {"source": "cluster", "instance_id": "web-01"}
        self.assertFalse(self.driver.validate(payload))


class ClusterDriverParseTests(TestCase):
    """Tests for ClusterDriver.parse()."""

    def setUp(self):
        self.driver = ClusterDriver()

    def test_parse_single_alert(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "hostname": "ip-10-0-1-42",
            "version": "1.0",
            "alerts": [
                {
                    "fingerprint": "cpu-check-web01",
                    "name": "CPU usage critical",
                    "status": "firing",
                    "severity": "critical",
                    "started_at": "2026-03-29T12:00:00Z",
                    "labels": {"checker": "cpu", "hostname": "ip-10-0-1-42"},
                    "annotations": {"message": "CPU at 95.2%"},
                    "metrics": {"cpu_percent": 95.2},
                }
            ],
        }
        result = self.driver.parse(payload)

        self.assertEqual(result.source, "cluster")
        self.assertEqual(len(result.alerts), 1)

        alert = result.alerts[0]
        self.assertEqual(alert.name, "CPU usage critical")
        self.assertEqual(alert.status, "firing")
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.fingerprint, "cpu-check-web01")
        self.assertEqual(alert.labels["instance_id"], "web-01")
        self.assertEqual(alert.labels["hostname"], "ip-10-0-1-42")

    def test_parse_multiple_alerts(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {"name": "CPU high", "status": "firing", "severity": "warning"},
                {"name": "Disk full", "status": "firing", "severity": "critical"},
            ],
        }
        result = self.driver.parse(payload)
        self.assertEqual(len(result.alerts), 2)

    def test_parse_injects_instance_id_into_labels(self):
        payload = {
            "source": "cluster",
            "instance_id": "db-server-03",
            "hostname": "db03.internal",
            "alerts": [
                {"name": "Memory high", "status": "firing", "labels": {"checker": "memory"}},
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.labels["instance_id"], "db-server-03")
        self.assertEqual(alert.labels["hostname"], "db03.internal")
        self.assertEqual(alert.labels["checker"], "memory")

    def test_parse_preserves_metrics_in_annotations(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {
                    "name": "CPU high",
                    "status": "firing",
                    "metrics": {"cpu_percent": 95.2, "load_avg": 4.5},
                },
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertIn("metrics", alert.annotations)

    def test_parse_generates_fingerprint_when_missing(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "Test Alert", "status": "firing"}],
        }
        result = self.driver.parse(payload)
        self.assertTrue(len(result.alerts[0].fingerprint) > 0)

    def test_parse_resolved_alert(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {
                    "name": "CPU OK",
                    "status": "resolved",
                    "ended_at": "2026-03-29T13:00:00Z",
                },
            ],
        }
        result = self.driver.parse(payload)
        alert = result.alerts[0]
        self.assertEqual(alert.status, "resolved")
        self.assertIsNotNone(alert.ended_at)

    def test_driver_name_is_cluster(self):
        self.assertEqual(self.driver.name, "cluster")

    def test_signature_header(self):
        self.assertEqual(self.driver.signature_header, "X-Cluster-Signature")

    def test_non_dict_labels_become_empty(self):
        """Labels that aren't a dict should be replaced with empty dict."""
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "Bad Labels", "status": "firing", "labels": "not-a-dict"}],
        }
        result = self.driver.parse(payload)
        self.assertEqual(result.alerts[0].labels["instance_id"], "web-01")

    def test_non_dict_annotations_become_empty(self):
        """Annotations that aren't a dict should be replaced with empty dict."""
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "Bad Annot", "status": "firing", "annotations": "not-a-dict"}],
        }
        result = self.driver.parse(payload)
        self.assertIsInstance(result.alerts[0].annotations, dict)

    def test_parse_timestamp_datetime_passthrough(self):
        """A datetime value should be returned as-is."""
        from datetime import datetime
        from datetime import timezone as dt_tz

        dt = datetime(2026, 3, 29, 12, 0, 0, tzinfo=dt_tz.utc)
        result = self.driver._parse_timestamp(dt)
        self.assertEqual(result, dt)

    def test_parse_timestamp_invalid_string_returns_now(self):
        """Invalid timestamp string should fall back to now."""
        from django.utils import timezone

        before = timezone.now()
        result = self.driver._parse_timestamp("garbage")
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    def test_parse_timestamp_non_string_returns_now(self):
        """Non-string, non-datetime timestamp should fall back to now."""
        from django.utils import timezone

        before = timezone.now()
        result = self.driver._parse_timestamp(12345)
        after = timezone.now()
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)

    def test_parse_without_hostname(self):
        """When hostname is empty, it should not be in labels."""
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "hostname": "",
            "alerts": [{"name": "Test", "status": "firing"}],
        }
        result = self.driver.parse(payload)
        self.assertNotIn("hostname", result.alerts[0].labels)


class ClusterDriverRegistrationTests(TestCase):
    """Tests for conditional driver registration."""

    @override_settings(CLUSTER_ENABLED=True)
    def test_driver_registered_when_enabled(self):
        from apps.alerts.drivers import DRIVER_REGISTRY, _register_cluster_driver

        original_cluster = DRIVER_REGISTRY.get("cluster")
        try:
            DRIVER_REGISTRY.pop("cluster", None)
            _register_cluster_driver()
            self.assertIn("cluster", DRIVER_REGISTRY)
            self.assertEqual(DRIVER_REGISTRY["cluster"], ClusterDriver)
        finally:
            if original_cluster is not None:
                DRIVER_REGISTRY["cluster"] = original_cluster
            else:
                DRIVER_REGISTRY.pop("cluster", None)

    @override_settings(CLUSTER_ENABLED=False)
    def test_driver_not_registered_when_disabled(self):
        from apps.alerts.drivers import DRIVER_REGISTRY, _register_cluster_driver

        original_cluster = DRIVER_REGISTRY.get("cluster")
        try:
            DRIVER_REGISTRY.pop("cluster", None)
            _register_cluster_driver()
            self.assertNotIn("cluster", DRIVER_REGISTRY)
        finally:
            if original_cluster is not None:
                DRIVER_REGISTRY["cluster"] = original_cluster
            else:
                DRIVER_REGISTRY.pop("cluster", None)

    @override_settings(CLUSTER_ENABLED=False)
    def test_driver_accessible_by_direct_import(self):
        """ClusterDriver can always be imported directly."""
        from apps.alerts.drivers.cluster import ClusterDriver as CD

        self.assertEqual(CD.name, "cluster")
