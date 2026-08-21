from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertSeverity, AlertStatus, Incident, IncidentStatus


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

    def test_alert_context_key_defaults_to_empty(self):
        alert = Alert.objects.create(
            fingerprint="ck-default",
            source="test",
            name="CPU Check Alert",
            severity=AlertSeverity.WARNING,
            status=AlertStatus.FIRING,
            started_at=timezone.now(),
        )

        self.assertEqual(alert.context_key, "")

    def test_alert_context_key_survives_an_update_fields_save(self):
        alert = Alert.objects.create(
            fingerprint="ck-update",
            source="test",
            name="CPU Check Alert",
            severity=AlertSeverity.WARNING,
            status=AlertStatus.FIRING,
            started_at=timezone.now(),
        )

        alert.context_key = "22,8080"
        alert.save(update_fields=["context_key"])
        alert.refresh_from_db()

        self.assertEqual(alert.context_key, "22,8080")


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


class IncidentReopenTests(TestCase):
    """reopen() is the inverse of resolve()/close(), for an alert that fired again."""

    def _incident(self, status):
        return Incident.objects.create(title="t", severity="critical", status=status)

    def test_reopen_from_resolved_clears_resolved_at(self):
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve(summary="done")

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.resolved_at)

    def test_reopen_from_closed_clears_closed_at(self):
        incident = self._incident(IncidentStatus.OPEN)
        incident.close()

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.closed_at)

    def test_reopen_keeps_the_summary(self):
        """The old summary is history, not something a reopen should erase."""
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve(summary="was fixed by restarting")

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.summary, "was fixed by restarting")

    def test_reopen_without_save_does_not_write(self):
        """Mirrors resolve()/close(): save=False lets a caller batch the write."""
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve()

        incident.reopen(save=False)

        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertEqual(Incident.objects.get(pk=incident.pk).status, IncidentStatus.RESOLVED)
