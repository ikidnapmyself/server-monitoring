from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, AlertStatus, Incident, IncidentStatus


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
