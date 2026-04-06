"""Tests for database vs config state checks."""

from django.test import TestCase, override_settings

from apps.checkers.status.database_checks import run
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition


class DatabaseChecksTests(TestCase):
    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_active_pipeline_with_eager_celery(self):
        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("eager" in r.message.lower() for r in warns))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_active_pipeline_without_eager_is_ok(self):
        PipelineDefinition.objects.create(name="test", config={}, is_active=True)
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        results = run()
        errors = [r for r in results if r.level in ("error", "warn")]
        self.assertFalse(any("eager" in r.message.lower() for r in errors))

    def test_no_active_channels(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("notification channel" in r.message.lower() for r in warns))

    def test_active_channel_present(self):
        NotificationChannel.objects.create(name="ch", driver="slack", is_active=True)
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertFalse(any("notification channel" in r.message.lower() for r in warns))

    def test_no_active_definitions(self):
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("pipeline definition" in r.message.lower() for r in infos))

    @override_settings(ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=False)
    def test_intelligence_active_fallback_disabled(self):
        IntelligenceProvider.objects.create(name="test-ai", provider="claude", is_active=True)
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("fallback" in r.message.lower() for r in infos))
