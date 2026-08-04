"""Tests for the system status dashboard renderer."""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.preflight.dashboard import (
    get_definitions,
    get_pipeline_state,
    get_profile,
)
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition, PipelineRun


class GetProfileTests(TestCase):
    @override_settings(
        HUB_URL="https://hub.example.com",
        API_KEY_AUTH_ENABLED=False,
        DEBUG=False,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "/tmp/db.sqlite3",
            }
        },
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="node-1",
        LOGS_DIR="/var/log/sm",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "bare"})
    def test_agent_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "agent")
        self.assertFalse(profile["receiving"])
        self.assertEqual(profile["hub_url"], "https://hub.example.com")
        self.assertEqual(profile["environment"], "prod")
        self.assertFalse(profile["debug"])
        self.assertEqual(profile["deploy_method"], "bare")
        self.assertEqual(profile["instance_id"], "node-1")

    @override_settings(HUB_URL="", API_KEY_AUTH_ENABLED=True)
    def test_hub_profile(self):
        from config.models import APIKey

        APIKey.objects.create(name="agent-x")  # active by default
        profile = get_profile()
        self.assertEqual(profile["role"], "hub")
        self.assertTrue(profile["receiving"])

    @override_settings(
        HUB_URL="",
        API_KEY_AUTH_ENABLED=True,
        DEBUG=True,
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_standalone_profile(self):
        # Auth on but no active keys → not receiving → standalone.
        profile = get_profile()
        self.assertEqual(profile["role"], "standalone")
        self.assertFalse(profile["receiving"])
        self.assertTrue(profile["debug"])

    @override_settings(HUB_URL="https://hub.example.com", API_KEY_AUTH_ENABLED=True)
    def test_agent_and_hub_profile(self):
        from config.models import APIKey

        APIKey.objects.create(name="agent-x")
        profile = get_profile()
        self.assertEqual(profile["role"], "agent+hub")  # valid, not a conflict
        self.assertTrue(profile["receiving"])

    @override_settings(HUB_URL="", API_KEY_AUTH_ENABLED=False)
    def test_receiving_requires_auth_enabled(self):
        from config.models import APIKey

        APIKey.objects.create(name="agent-x")  # active, but auth is off
        profile = get_profile()
        self.assertEqual(profile["role"], "standalone")
        self.assertFalse(profile["receiving"])


class GetPipelineStateTests(TestCase):
    def test_empty_state(self):
        state = get_pipeline_state()
        self.assertEqual(state["channels"], [])
        self.assertEqual(state["intelligence"], [])
        self.assertIsNone(state["last_run"])

    def test_with_channels_and_providers(self):
        NotificationChannel.objects.create(name="slack", driver="slack", is_active=True)
        NotificationChannel.objects.create(name="email", driver="email", is_active=False)
        IntelligenceProvider.objects.create(name="ai", provider="claude", is_active=True)
        state = get_pipeline_state()
        self.assertEqual(len(state["channels"]), 2)
        self.assertEqual(len(state["intelligence"]), 1)

    def test_last_run(self):
        PipelineRun.objects.create(trace_id="t1", run_id="r1", status="notified")
        state = get_pipeline_state()
        self.assertIsNotNone(state["last_run"])
        self.assertEqual(state["last_run"]["status"], "notified")


class GetDefinitionsTests(TestCase):
    def test_returns_routing_fields(self):
        ch = NotificationChannel.objects.create(
            name="ops", driver="slack", config={"webhook_url": "https://hooks.slack.com/x"}
        )
        defn = PipelineDefinition.objects.create(name="pipe1", priority=5, is_active=True)
        defn.channels.add(ch)

        defs = get_definitions()
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0]["name"], "pipe1")
        self.assertTrue(defs[0]["active"])
        self.assertEqual(defs[0]["priority"], 5)
        self.assertEqual(defs[0]["channels"], 1)
