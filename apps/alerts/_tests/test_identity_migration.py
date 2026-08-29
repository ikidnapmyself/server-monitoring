"""Tests for the checker-alert identity backfill (migration 0011).

The helper is tested directly as a pure function, and the migration's
``forward`` body is exercised against real ORM rows via a stand-in ``apps``
object — ``forward`` only ever needs ``get_model``.
"""

from importlib import import_module

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.alerts.identity import new_fingerprint_for
from apps.alerts.models import Alert

# Migration module names start with a digit, so they cannot be imported with a
# plain ``from ... import`` statement.
forward = import_module("apps.alerts.migrations.0011_checker_alert_identity").forward


class NewFingerprintForTests(SimpleTestCase):
    def test_uses_instance_id_label_when_present(self):
        self.assertEqual(
            new_fingerprint_for(
                {"checker": "cpu", "instance_id": "n1", "hostname": "h1"}, "fallback"
            ),
            "check:n1:cpu",
        )

    def test_falls_back_to_hostname_label(self):
        self.assertEqual(
            new_fingerprint_for({"checker": "cpu", "hostname": "h1"}, "fallback"),
            "check:h1:cpu",
        )

    def test_falls_back_to_local_instance_when_no_machine_label(self):
        self.assertEqual(new_fingerprint_for({"checker": "cpu"}, "fallback"), "check:fallback:cpu")

    def test_returns_none_without_a_checker_label(self):
        self.assertIsNone(new_fingerprint_for({"hostname": "h1"}, "fallback"))

    def test_returns_none_for_missing_labels(self):
        self.assertIsNone(new_fingerprint_for(None, "fallback"))

    def test_blank_instance_id_label_falls_through_to_hostname(self):
        self.assertEqual(
            new_fingerprint_for({"checker": "cpu", "instance_id": "", "hostname": "h1"}, "fb"),
            "check:h1:cpu",
        )

    def test_non_dict_labels_are_treated_as_absent(self):
        # webhook payloads are attacker-controlled; labels can be a string
        self.assertIsNone(new_fingerprint_for("not-a-dict", "fallback"))

    def test_hostname_is_skipped_when_the_caller_declines_it(self):
        self.assertEqual(
            new_fingerprint_for({"checker": "cpu", "hostname": "remote"}, "fb", use_hostname=False),
            "check:fb:cpu",
        )

    def test_instance_id_still_wins_when_hostname_is_declined(self):
        self.assertEqual(
            new_fingerprint_for(
                {"checker": "cpu", "instance_id": "n1", "hostname": "remote"},
                "fb",
                use_hostname=False,
            ),
            "check:n1:cpu",
        )


class _FakeApps:
    """Minimal stand-in for the migration's ``apps`` registry."""

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("alerts", "Alert")
        return Alert


class ForwardMigrationTests(TestCase):
    def _forward(self):
        forward(_FakeApps(), None)

    def test_rewrites_a_legacy_push_alert(self):
        alert = Alert.objects.create(
            fingerprint="cpu-web-01",
            source="cluster",
            name="CPU high",
            started_at=timezone.now(),
            labels={"checker": "cpu", "hostname": "web-01", "instance_id": "n1"},
        )
        self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "check:n1:cpu")
        self.assertEqual(alert.source, "cluster")

    def test_rewrites_a_legacy_bridge_alert_and_its_source(self):
        alert = Alert.objects.create(
            fingerprint="0123456789abcdef",
            source="server-checkers",
            name="Disk high",
            started_at=timezone.now(),
            labels={"checker": "disk", "hostname": "hub-01", "metric_used": "91"},
        )
        with override_settings(INSTANCE_ID="hub-01"):
            self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "check:hub-01:disk")
        self.assertEqual(alert.source, "cluster")

    def test_bridge_row_with_a_remote_hostname_migrates_to_the_local_instance(self):
        # Hub-side diagnosis labels alerts with the SUBJECT incident's hostname
        # while running the checkers here, so that label must not become identity.
        alert = Alert.objects.create(
            fingerprint="fedcba9876543210",
            source="server-checkers",
            name="CPU high",
            labels={"checker": "cpu", "hostname": "remote-box"},
            started_at=timezone.now(),
        )
        with override_settings(INSTANCE_ID="hub-1"):
            self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "check:hub-1:cpu")

    def test_bridge_row_with_an_instance_id_label_keeps_it(self):
        alert = Alert.objects.create(
            fingerprint="aaaabbbbccccdddd",
            source="server-checkers",
            name="CPU high",
            labels={"checker": "cpu", "instance_id": "n9", "hostname": "remote-box"},
            started_at=timezone.now(),
        )
        with override_settings(INSTANCE_ID="hub-1"):
            self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "check:n9:cpu")

    def test_cluster_row_with_only_a_hostname_label_uses_that_hostname(self):
        alert = Alert.objects.create(
            fingerprint="cpu-web-01",
            source="cluster",
            name="CPU high",
            labels={"checker": "cpu", "hostname": "web-01"},
            started_at=timezone.now(),
        )
        with override_settings(INSTANCE_ID="hub-1"):
            self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "check:web-01:cpu")

    def test_leaves_webhook_alerts_alone(self):
        alert = Alert.objects.create(
            fingerprint="abc123",
            source="grafana",
            name="Latency",
            started_at=timezone.now(),
            labels={"hostname": "web-01"},
        )
        self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "abc123")
        self.assertEqual(alert.source, "grafana")

    def test_checker_origin_row_without_a_checker_label_is_untouched(self):
        alert = Alert.objects.create(
            fingerprint="legacy-blob",
            source="cluster",
            name="Something",
            started_at=timezone.now(),
            labels={"hostname": "web-01"},
        )
        self._forward()
        alert.refresh_from_db()
        self.assertEqual(alert.fingerprint, "legacy-blob")

    def test_colliding_rows_do_not_merge(self):
        older = Alert.objects.create(
            fingerprint="cpu-web-01",
            source="cluster",
            name="CPU high",
            started_at=timezone.now(),
            labels={"checker": "cpu", "hostname": "web-01"},
        )
        newer = Alert.objects.create(
            fingerprint="0123456789abcdef",
            source="server-checkers",
            name="CPU high",
            started_at=timezone.now(),
            labels={"checker": "cpu", "instance_id": "web-01"},
        )
        # received_at is auto-set; force a deterministic ordering.
        Alert.objects.filter(pk=older.pk).update(received_at=_earlier(older))
        self._forward()
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(newer.fingerprint, "check:web-01:cpu")
        self.assertEqual(older.fingerprint, f"check:web-01:cpu:legacy:{older.pk}")
        self.assertEqual(older.source, "cluster")

    def test_a_parked_row_is_resolved_so_it_cannot_hang_open(self):
        """A parked fingerprint is a value no producer will ever emit again.

        _process_alert looks a row up by ``(fingerprint, source)``, so nothing
        would ever find a parked row to resolve it, and its incident would stay
        open forever. Parking therefore closes the row on the way past;
        _check_incident_resolution closes the incident once all its alerts are.
        """
        older = Alert.objects.create(
            fingerprint="cpu-web-01",
            source="cluster",
            status="firing",
            name="CPU high",
            started_at=timezone.now(),
            labels={"checker": "cpu", "hostname": "web-01"},
        )
        newer = Alert.objects.create(
            fingerprint="0123456789abcdef",
            source="server-checkers",
            status="firing",
            name="CPU high",
            started_at=timezone.now(),
            labels={"checker": "cpu", "instance_id": "web-01"},
        )
        Alert.objects.filter(pk=older.pk).update(received_at=_earlier(older))
        self._forward()
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.status, "resolved")
        self.assertIsNotNone(older.ended_at)
        # The winner keeps the identity and stays exactly as it was.
        self.assertEqual(newer.status, "firing")
        self.assertIsNone(newer.ended_at)


def _earlier(alert):
    from datetime import timedelta

    return alert.received_at - timedelta(hours=1)
