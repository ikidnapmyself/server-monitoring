"""Tests for the opened_at backfill (migration 0014)."""

from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from apps.alerts.models import Incident

MIGRATION = import_module("apps.alerts.migrations.0014_incident_opened_at")


class _Apps:
    def get_model(self, app_label, model_name):
        return django_apps.get_model(app_label, model_name)


class OpenedAtBackfillTests(TestCase):
    def test_every_incident_opens_when_it_was_created(self):
        first = timezone.now() - timedelta(days=21)
        incident = Incident.objects.create(subject="cpu")
        Incident.objects.filter(pk=incident.pk).update(created_at=first)

        MIGRATION.backfill_opened_at(_Apps(), None)

        incident.refresh_from_db()
        self.assertEqual(incident.opened_at, first)
