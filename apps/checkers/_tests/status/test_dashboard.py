"""Tests for the system status dashboard renderer."""

import os
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.checkers.status.dashboard import (
    get_definitions,
    get_pipeline_state,
    get_profile,
    render_definition_chain,
)
from apps.intelligence.models import IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import PipelineDefinition, PipelineRun


class GetProfileTests(TestCase):
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        DEBUG=False,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "/tmp/db.sqlite3",
            }
        },
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="node-1",
        LOGS_DIR="/var/log/sm",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "bare"})
    def test_agent_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "agent")
        self.assertEqual(profile["hub_url"], "https://hub.example.com")
        self.assertEqual(profile["environment"], "prod")
        self.assertFalse(profile["debug"])
        self.assertEqual(profile["deploy_method"], "bare")
        self.assertEqual(profile["instance_id"], "node-1")

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        DEBUG=False,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "/tmp/db.sqlite3",
            }
        },
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="",
        LOGS_DIR="/var/log/sm",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "docker"})
    def test_hub_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "hub")

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "/tmp/db.sqlite3",
            }
        },
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=True,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="",
        LOGS_DIR="/tmp/logs",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "dev", "DEPLOY_METHOD": "bare"})
    def test_standalone_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "standalone")
        self.assertTrue(profile["debug"])
        self.assertTrue(profile["celery_eager"])

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        DEBUG=False,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "/tmp/db.sqlite3",
            }
        },
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=False,
        ORCHESTRATION_METRICS_BACKEND="logging",
        INSTANCE_ID="",
        LOGS_DIR="/tmp/logs",
    )
    @patch.dict(os.environ, {"DJANGO_ENV": "prod", "DEPLOY_METHOD": "bare"})
    def test_conflict_profile(self):
        profile = get_profile()
        self.assertEqual(profile["role"], "conflict")


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


class RenderDefinitionChainTests(TestCase):
    def test_renders_node_chain(self):
        defn = PipelineDefinition.objects.create(
            name="full",
            config={
                "nodes": [
                    {
                        "id": "ingest",
                        "type": "alerts",
                        "config": {"driver": "webhook"},
                        "next": "check",
                    },
                    {
                        "id": "check",
                        "type": "checkers",
                        "config": {"checkers": ["cpu", "memory"]},
                        "next": "notify",
                    },
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {"driver": "slack"},
                    },
                ]
            },
            is_active=True,
        )
        chain = render_definition_chain(defn)
        self.assertIn("alerts", chain)
        self.assertIn("cpu", chain)
        self.assertIn("notify", chain)
        self.assertIn("\u2192", chain)

    def test_renders_drivers_and_channels(self):
        defn = PipelineDefinition.objects.create(
            name="multi",
            config={
                "nodes": [
                    {
                        "id": "ingest",
                        "type": "alerts",
                        "config": {"drivers": ["webhook", "email"]},
                    },
                    {
                        "id": "notify",
                        "type": "notify",
                        "config": {"channels": ["slack", "pagerduty"]},
                    },
                ]
            },
            is_active=True,
        )
        chain = render_definition_chain(defn)
        self.assertIn("webhook,email", chain)
        self.assertIn("slack,pagerduty", chain)

    def test_node_without_config_details(self):
        defn = PipelineDefinition.objects.create(
            name="bare",
            config={
                "nodes": [
                    {"id": "transform", "type": "transform", "config": {}},
                ]
            },
            is_active=True,
        )
        chain = render_definition_chain(defn)
        self.assertEqual(chain, "transform")

    def test_empty_config(self):
        defn = PipelineDefinition.objects.create(name="empty", config={}, is_active=True)
        chain = render_definition_chain(defn)
        self.assertEqual(chain, "(no stages)")


class GetDefinitionsTests(TestCase):
    def test_returns_definitions_with_chain(self):
        PipelineDefinition.objects.create(
            name="pipe1",
            config={
                "nodes": [
                    {
                        "id": "n1",
                        "type": "notify",
                        "config": {"driver": "slack"},
                    }
                ]
            },
            is_active=True,
        )
        defs = get_definitions()
        self.assertEqual(len(defs), 1)
        self.assertEqual(defs[0]["name"], "pipe1")
        self.assertTrue(defs[0]["active"])
        self.assertIn("chain", defs[0])
        self.assertIn("stages", defs[0])
