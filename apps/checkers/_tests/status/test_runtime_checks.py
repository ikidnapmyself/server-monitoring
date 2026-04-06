"""Tests for runtime state consistency checks."""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.runtime_checks import run


class RuntimeChecksTests(TestCase):
    @override_settings(DEBUG=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_debug_on_in_production(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("DEBUG" in r.message for r in errors))

    @override_settings(DEBUG=False, ALLOWED_HOSTS=[])
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_no_allowed_hosts_in_production(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("ALLOWED_HOSTS" in r.message for r in errors))

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_celery_eager_in_production(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("eager" in r.message.lower() for r in warns))

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="custom-host",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_statsd_configured_but_backend_logging(self):
        results = run()
        infos = [r for r in results if r.level == "info"]
        self.assertTrue(any("StatsD" in r.message for r in infos))

    @override_settings(
        ORCHESTRATION_METRICS_BACKEND="statsd",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_statsd_backend_with_default_host(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("STATSD_HOST" in r.message for r in warns))

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["example.com"],
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod"})
    def test_clean_production(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        DEBUG=True,
        CELERY_TASK_ALWAYS_EAGER=True,
        ORCHESTRATION_METRICS_BACKEND="logging",
        STATSD_HOST="localhost",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev"})
    def test_dev_mode_allows_debug_and_eager(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
