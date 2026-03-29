from django.test import TestCase

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
