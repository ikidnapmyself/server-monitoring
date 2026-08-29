"""The hub derives checker-alert identity; it does not trust the push.

Auth is a shared API key with no per-node binding, so any holder can name any
``instance_id`` in a payload body. Identity decides which machine's Alert row
(and incident, and history) a push lands on, so the hub recomputes it from the
envelope it authenticated rather than reading the sender's ``fingerprint``.
"""

from django.test import TestCase
from django.utils import timezone

from apps.alerts.drivers.cluster import ClusterDriver
from apps.alerts.identity import checker_fingerprint
from apps.alerts.management.commands.push_to_hub import Command as PushToHubCommand
from apps.alerts.models import Alert, AlertSeverity, AlertStatus
from apps.alerts.services import AlertOrchestrator, register_pushing_node
from apps.checkers.checkers.base import CheckResult, CheckStatus


def _push(instance_id, claimed_fingerprint, checker="cpu", **alert_extra):
    alert = {
        "name": f"{checker.upper()} Check Alert",
        "status": "firing",
        "severity": "critical",
        "labels": {"checker": checker},
    }
    if claimed_fingerprint is not None:
        alert["fingerprint"] = claimed_fingerprint
    alert.update(alert_extra)
    return {
        "source": "cluster",
        "instance_id": instance_id,
        "hostname": f"{instance_id}.internal",
        "alerts": [alert],
    }


class SpoofedFingerprintTests(TestCase):
    """A sender must not be able to claim another machine's alert row."""

    def test_claimed_fingerprint_is_ignored_for_checker_alerts(self):
        AlertOrchestrator().process_webhook(
            _push("attacker", "check:victim-node:cpu"), driver="cluster"
        )
        alert = Alert.objects.get(source="cluster")
        self.assertEqual(alert.fingerprint, "check:attacker:cpu")

    def test_spoofed_push_does_not_touch_the_victims_row(self):
        victim = Alert.objects.create(
            fingerprint=checker_fingerprint("victim-node", "cpu"),
            source="cluster",
            name="CPU Check Alert",
            status=AlertStatus.FIRING,
            severity=AlertSeverity.WARNING,
            labels={"checker": "cpu", "instance_id": "victim-node"},
            started_at=timezone.now(),
        )

        AlertOrchestrator().process_webhook(
            _push("attacker", "check:victim-node:cpu"), driver="cluster"
        )

        victim.refresh_from_db()
        self.assertEqual(victim.severity, AlertSeverity.WARNING)
        self.assertEqual(victim.labels["instance_id"], "victim-node")
        self.assertEqual(victim.history.count(), 0)
        self.assertEqual(Alert.objects.filter(source="cluster").count(), 2)


class HonestPushUnchangedTests(TestCase):
    """An honest node computes the same value, so nothing about it moves."""

    def test_honest_fingerprint_is_the_value_the_node_sent(self):
        payload = _push("web-01", checker_fingerprint("web-01", "cpu"))
        parsed = ClusterDriver().parse(payload)
        self.assertEqual(parsed.alerts[0].fingerprint, "check:web-01:cpu")

    def test_repush_of_the_same_condition_creates_no_second_row(self):
        payload = _push("web-01", checker_fingerprint("web-01", "cpu"))
        orchestrator = AlertOrchestrator()
        orchestrator.process_webhook(payload, driver="cluster")
        orchestrator.process_webhook(payload, driver="cluster")
        self.assertEqual(Alert.objects.filter(source="cluster").count(), 1)


class NonCheckerAlertTests(TestCase):
    """No ``checker`` label means this is not checker-origin: leave it alone."""

    def test_provided_fingerprint_survives_without_a_checker_label(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [
                {"name": "Custom", "status": "firing", "fingerprint": "sender-chosen"},
            ],
        }
        parsed = ClusterDriver().parse(payload)
        self.assertEqual(parsed.alerts[0].fingerprint, "sender-chosen")

    def test_fingerprint_is_still_generated_when_absent(self):
        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "alerts": [{"name": "Custom", "status": "firing"}],
        }
        parsed = ClusterDriver().parse(payload)
        self.assertTrue(parsed.alerts[0].fingerprint)
        self.assertNotIn("check:", parsed.alerts[0].fingerprint)


class BlankInstanceIdTests(TestCase):
    """A blank id must not become a fingerprint or a Node row."""

    def setUp(self):
        self.driver = ClusterDriver()

    def test_validate_rejects_whitespace_only_instance_id(self):
        payload = {"source": "cluster", "instance_id": "   ", "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_rejects_non_string_instance_id(self):
        payload = {"source": "cluster", "instance_id": {"a": 1}, "alerts": []}
        self.assertFalse(self.driver.validate(payload))

    def test_validate_still_accepts_a_real_id(self):
        payload = {"source": "cluster", "instance_id": "web-01", "alerts": []}
        self.assertTrue(self.driver.validate(payload))

    def test_register_pushing_node_rejects_whitespace_only_instance_id(self):
        from apps.alerts.models import Node

        self.assertIsNone(
            register_pushing_node({"source": "cluster", "instance_id": "  ", "alerts": []})
        )
        self.assertEqual(Node.objects.count(), 0)


class NodeAndHubAgreeTests(TestCase):
    """The recomputed value is what the node-side producer emits."""

    def test_hub_recomputation_matches_push_to_hub(self):
        result = CheckResult(
            checker_name="cpu",
            status=CheckStatus.CRITICAL,
            message="CPU at 95.2%",
        )
        node_alert = PushToHubCommand()._result_to_alert(result, "web-01", "web-01.internal")

        payload = {
            "source": "cluster",
            "instance_id": "web-01",
            "hostname": "web-01.internal",
            "alerts": [node_alert],
        }
        parsed = ClusterDriver().parse(payload)
        self.assertEqual(parsed.alerts[0].fingerprint, node_alert["fingerprint"])
