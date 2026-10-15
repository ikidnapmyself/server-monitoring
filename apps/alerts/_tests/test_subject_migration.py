"""Tests for the subject backfill (migration 0012)."""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node

MIGRATION = import_module("apps.alerts.migrations.0012_subject_keys")
forward = MIGRATION.forward


class _Apps:
    def get_model(self, app_label, model_name):
        return django_apps.get_model(app_label, model_name)


def _alert(**kw):
    defaults = dict(
        fingerprint="fp",
        source="cluster",
        name="CPU Check Alert",
        severity="warning",
        status="firing",
        labels={},
        started_at=timezone.now(),
    )
    defaults.update(kw)
    return Alert.objects.create(**defaults)


class SubjectBackfillTests(TestCase):
    def test_checker_alert_gets_checker_subject(self):
        alert = _alert(labels={"checker": "cpu", "instance_id": "n1"})
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "checker:cpu")

    def test_webhook_alert_gets_source_and_name(self):
        alert = _alert(source="alertmanager", name="HighCPU", labels={"instance": "w1"})
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "alertmanager:HighCPU")

    def test_incident_takes_its_oldest_alerts_keys(self):
        node = Node.objects.create(instance_id="n1", hostname="h1")
        incident = Incident.objects.create(title="t", severity="warning")
        _alert(
            fingerprint="a",
            labels={"checker": "cpu", "instance_id": "n1"},
            node=node,
            incident=incident,
        )
        _alert(fingerprint="b", labels={"checker": "cpu", "instance_id": "n2"}, incident=incident)
        forward(_Apps(), None)
        incident.refresh_from_db()
        self.assertEqual(
            (incident.subject, incident.instance, incident.node_id),
            ("checker:cpu", "n1", node.pk),
        )

    def test_incident_without_alerts_is_left_blank(self):
        incident = Incident.objects.create(title="t", severity="warning")
        forward(_Apps(), None)
        incident.refresh_from_db()
        self.assertEqual((incident.subject, incident.instance), ("", ""))

    def test_non_dict_labels_do_not_raise(self):
        alert = _alert(source="generic", name="X", labels="pwned")
        forward(_Apps(), None)
        alert.refresh_from_db()
        self.assertEqual(alert.subject, "generic:X")
