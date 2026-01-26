"""Tests for GenericNotifyDriver payload building and config validation."""

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.generic import GenericNotifyDriver


class GenericDriverTests(SimpleTestCase):
    def test_generic_validate_config(self):
        d = GenericNotifyDriver()
        self.assertTrue(d.validate_config({}))
        self.assertTrue(d.validate_config({"endpoint": "http://example.com/hook"}))
        self.assertTrue(d.validate_config({"webhook_url": "https://example.com/hook"}))
        self.assertFalse(d.validate_config({"endpoint": "not-a-url"}))

    def test_generic_build_payload_from_template_text(self):
        d = GenericNotifyDriver()
        msg = NotificationMessage(title="T", message="M", severity="info")
        # Use the generic_payload.j2 template by specifying file: generic_payload.j2
        cfg = {"payload_template": "file:generic_payload.j2"}
        payload = d._build_payload(msg, cfg)
        self.assertIn("title", payload)
        self.assertEqual(payload["title"], "T")
        self.assertIn("incident", payload)
