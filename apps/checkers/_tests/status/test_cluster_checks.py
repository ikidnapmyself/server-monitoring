"""Tests for cluster profile coherence checks."""

from django.test import TestCase, override_settings

from apps.checkers.status.cluster_checks import run


class ClusterChecksTests(TestCase):
    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_agent_and_hub_conflict(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("conflict" in r.message.lower() for r in errors))

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="node-1",
    )
    def test_agent_without_secret(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in warns))

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_agent_without_instance_id(self):
        results = run()
        warns = [r for r in results if r.level == "warn"]
        self.assertTrue(any("INSTANCE_ID" in r.message for r in warns))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_hub_without_secret(self):
        results = run()
        errors = [r for r in results if r.level == "error"]
        self.assertTrue(any("WEBHOOK_SECRET_CLUSTER" in r.message for r in errors))

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="",
        INSTANCE_ID="",
    )
    def test_standalone_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        HUB_URL="https://hub.example.com",
        CLUSTER_ENABLED=False,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="node-1",
    )
    def test_valid_agent_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)

    @override_settings(
        HUB_URL="",
        CLUSTER_ENABLED=True,
        WEBHOOK_SECRET_CLUSTER="secret",
        INSTANCE_ID="",
    )
    def test_valid_hub_is_ok(self):
        results = run()
        levels = {r.level for r in results}
        self.assertNotIn("error", levels)
        self.assertNotIn("warn", levels)
