"""Tests for Email driver helpers (no real SMTP calls)."""

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver


class EmailDriverTests(SimpleTestCase):
    def test_email_validate_config_and_build_email(self):
        d = EmailNotifyDriver()
        self.assertFalse(d.validate_config({}))
        cfg = {"smtp_host": "smtp.example.local", "from_address": "noreply@example.local"}
        self.assertTrue(d.validate_config(cfg))

        msg = NotificationMessage(
            title="T", message="M", severity="warning", channel="ops@example.local"
        )
        email = d._build_email(msg, cfg)
        self.assertIn("Subject", email)
        self.assertIn("From", email)
        self.assertIn("To", email)
        self.assertTrue(email.get_payload())  # has parts
