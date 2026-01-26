"""Tests for PagerDuty driver payload building and config validation."""

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver


class PagerDutyDriverTests(SimpleTestCase):
    def test_pagerduty_validate_config_and_payload(self):
        d = PagerDutyNotifyDriver()
        self.assertTrue(d.validate_config({"integration_key": "x" * 32}))
        self.assertFalse(d.validate_config({}))

        msg = NotificationMessage(
            title="T", message="M", severity="critical", tags={"fingerprint": "fp1"}
        )
        cfg = {"integration_key": "k" * 32, "client": "test-client"}
        payload = d._build_payload(msg, cfg)
        self.assertEqual(payload["routing_key"], cfg["integration_key"])
        self.assertIn("payload", payload)
        self.assertIsNotNone(payload["incident"])
        # dedup_key should come from message.tags fingerprint
        self.assertEqual(payload.get("dedup_key"), "fp1")
