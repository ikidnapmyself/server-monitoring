from datetime import timedelta

import pytest
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.alerts.models import Alert, AlertStatus, Incident, IncidentStatus, Node


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


@pytest.mark.django_db
def test_node_is_self_defaults_false():
    node = Node.objects.create(instance_id="agent-1")
    assert node.is_self is False


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_creates_self_node():
    node = Node.ensure_self()
    assert node is not None
    assert node.instance_id == "hub-xyz"
    assert node.is_self is True


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_is_idempotent():
    first = Node.ensure_self()
    second = Node.ensure_self()
    assert first.pk == second.pk
    assert Node.objects.filter(is_self=True).count() == 1


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_ensure_self_noop_when_instance_id_unset():
    assert Node.ensure_self() is None
    assert Node.objects.filter(is_self=True).count() == 0
