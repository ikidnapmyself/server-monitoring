"""Tests for Email driver helpers (no real SMTP calls)."""

import smtplib
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.email import EmailNotifyDriver


def _base_config(**overrides):
    """Return a minimal valid email config, merged with overrides."""
    cfg = {
        "smtp_host": "smtp.example.local",
        "from_address": "noreply@example.local",
    }
    cfg.update(overrides)
    return cfg


def _msg(**overrides):
    """Return a default NotificationMessage, merged with overrides."""
    defaults = {
        "title": "Test Alert",
        "message": "Something happened",
        "severity": "warning",
        "channel": "ops@example.local",
    }
    defaults.update(overrides)
    return NotificationMessage(**defaults)


class EmailDriverValidationTests(SimpleTestCase):
    """Tests for validate_config()."""

    def test_validate_config_missing_keys(self):
        d = EmailNotifyDriver()
        self.assertFalse(d.validate_config({}))
        self.assertFalse(d.validate_config({"smtp_host": "h"}))
        self.assertFalse(d.validate_config({"from_address": "a"}))

    def test_validate_config_valid(self):
        d = EmailNotifyDriver()
        self.assertTrue(d.validate_config(_base_config()))


class EmailBuildEmailTests(SimpleTestCase):
    """Tests for _build_email()."""

    def test_build_email_basic_headers(self):
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()
        email = d._build_email(msg, cfg)
        self.assertIn("Subject", email)
        self.assertEqual(email["From"], "noreply@example.local")
        self.assertIn("To", email)
        self.assertTrue(email.get_payload())

    def test_build_email_to_addresses_from_config(self):
        """When to_addresses is provided in config, it should be used directly."""
        d = EmailNotifyDriver()
        cfg = _base_config(to_addresses=["team@example.local"])
        msg = _msg()
        email = d._build_email(msg, cfg)
        self.assertEqual(email["To"], "team@example.local")

    def test_build_email_to_fallback_channel(self):
        """When to_addresses is empty and channel != 'default', use channel."""
        d = EmailNotifyDriver()
        cfg = _base_config()  # no to_addresses
        msg = _msg(channel="ops@example.local")
        email = d._build_email(msg, cfg)
        self.assertEqual(email["To"], "ops@example.local")

    def test_build_email_to_fallback_from_address(self):
        """When to_addresses is empty and channel == 'default', use from_address."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg(channel="default")
        email = d._build_email(msg, cfg)
        self.assertEqual(email["To"], "noreply@example.local")

    def test_build_email_no_text_body_raises(self):
        """When _prepare_notification returns no text, a ValueError is raised."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()
        with patch.object(d, "_prepare_notification", return_value={"text": None}):
            with self.assertRaises(ValueError) as ctx:
                d._build_email(msg, cfg)
            self.assertIn("text template required", str(ctx.exception))

    def test_build_email_html_body_attached(self):
        """When html is rendered, it's attached to the email."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()
        prepared = {
            "text": "plain body",
            "html": "<h1>HTML body</h1>",
            "incident": {"id": "123"},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            email = d._build_email(msg, cfg)
        payloads = email.get_payload()
        content_types = [p.get_content_type() for p in payloads]
        self.assertIn("text/plain", content_types)
        self.assertIn("text/html", content_types)

    def test_build_email_no_html_body(self):
        """When html is None, only plain text is attached (plus incident JSON)."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()
        prepared = {
            "text": "plain body",
            "html": None,
            "incident": {"id": "123"},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            email = d._build_email(msg, cfg)
        payloads = email.get_payload()
        content_types = [p.get_content_type() for p in payloads]
        self.assertNotIn("text/html", content_types)

    def test_build_email_incident_json_attached(self):
        """Incident JSON is serialized and attached as a text part."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()
        prepared = {
            "text": "plain body",
            "html": None,
            "incident": {"alert_id": "abc-123"},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            email = d._build_email(msg, cfg)
        payloads = email.get_payload()
        # First is plain text body, second is incident JSON
        self.assertEqual(len(payloads), 2)
        json_part = payloads[1].get_payload()
        self.assertIn("abc-123", json_part)

    def test_build_email_incident_json_serialization_error(self):
        """When incident JSON serialization fails, it is silently ignored."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()

        prepared = {
            "text": "plain body",
            "html": None,
            "incident": None,
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            with patch(
                "apps.notify.drivers.email.json.dumps",
                side_effect=TypeError("boom"),
            ):
                email = d._build_email(msg, cfg)
        # Should still succeed -- only text body attached
        payloads = email.get_payload()
        self.assertEqual(len(payloads), 1)

    def test_build_email_incident_json_empty_string_not_attached(self):
        """When json.dumps returns empty string, no attachment is added."""
        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg()

        prepared = {
            "text": "plain body",
            "html": None,
            "incident": {},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            with patch("apps.notify.drivers.email.json.dumps", return_value=""):
                email = d._build_email(msg, cfg)
        # Empty string is falsy, so no incident attachment
        payloads = email.get_payload()
        self.assertEqual(len(payloads), 1)


class EmailSendTests(SimpleTestCase):
    """Tests for send() -- all SMTP calls mocked."""

    def _mock_smtp(self):
        """Create a mock SMTP instance with all needed methods."""
        mock = MagicMock()
        mock.starttls = MagicMock()
        mock.login = MagicMock()
        mock.sendmail = MagicMock()
        mock.quit = MagicMock()
        return mock

    def test_send_invalid_config(self):
        """send() returns error when config is invalid."""
        d = EmailNotifyDriver()
        result = d.send(_msg(), {})
        self.assertFalse(result["success"])
        self.assertIn("Invalid email configuration", result["error"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_success_with_tls(self, mock_smtp_cls):
        """Successful send with TLS (default config)."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(to_addresses=["recipient@example.local"])
        msg = _msg()

        prepared = {
            "text": "Alert body",
            "html": None,
            "incident": {},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        self.assertIn("message_id", result)
        self.assertIn("metadata", result)
        self.assertEqual(result["metadata"]["to"], ["recipient@example.local"])

        # TLS should be started
        mock_server.starttls.assert_called_once()
        # No auth by default
        mock_server.login.assert_not_called()
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("apps.notify.drivers.email.smtplib.SMTP_SSL")
    def test_send_success_with_ssl(self, mock_smtp_ssl_cls):
        """Successful send with SSL."""
        mock_server = self._mock_smtp()
        mock_smtp_ssl_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            use_ssl=True,
            use_tls=False,
            to_addresses=["recipient@example.local"],
        )
        msg = _msg()

        prepared = {
            "text": "Alert body",
            "html": None,
            "incident": {},
        }
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_smtp_ssl_cls.assert_called_once_with("smtp.example.local", 587, timeout=30)
        # starttls should NOT be called when use_ssl=True
        mock_server.starttls.assert_not_called()
        mock_server.quit.assert_called_once()

    @patch("apps.notify.drivers.email.smtplib.SMTP_SSL")
    def test_send_ssl_with_tls_flag_does_not_starttls(self, mock_smtp_ssl_cls):
        """When use_ssl=True, starttls is skipped even if use_tls=True."""
        mock_server = self._mock_smtp()
        mock_smtp_ssl_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            use_ssl=True,
            use_tls=True,
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_server.starttls.assert_not_called()

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_no_tls(self, mock_smtp_cls):
        """When use_tls=False (and use_ssl=False), starttls is not called."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            use_tls=False,
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_server.starttls.assert_not_called()

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_with_auth(self, mock_smtp_cls):
        """When username and password are provided, login is called."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            username="user@example.local",
            password="secret",
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_server.login.assert_called_once_with("user@example.local", "secret")

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_no_auth_when_username_missing(self, mock_smtp_cls):
        """When only password is given (no username), login is not called."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            password="secret",
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_server.login.assert_not_called()

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_to_fallback_channel(self, mock_smtp_cls):
        """When no to_addresses in config and channel is not 'default'."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config()  # no to_addresses
        msg = _msg(channel="oncall@example.local")

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["to"], ["oncall@example.local"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_to_fallback_from_address(self, mock_smtp_cls):
        """When no to_addresses and channel == 'default', use from_address."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config()
        msg = _msg(channel="default")

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["to"], ["noreply@example.local"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_smtp_authentication_error(self, mock_smtp_cls):
        """SMTPAuthenticationError is caught and returned as error."""
        mock_server = self._mock_smtp()
        mock_server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"Authentication failed"
        )
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            username="user",
            password="bad",
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertFalse(result["success"])
        self.assertIn("authenticate SMTP", result["error"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_smtp_exception(self, mock_smtp_cls):
        """SMTPException is caught and returned as error."""
        mock_server = self._mock_smtp()
        mock_server.sendmail.side_effect = smtplib.SMTPException("Connection lost")
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(to_addresses=["r@example.local"])
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertFalse(result["success"])
        self.assertIn("send SMTP", result["error"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_generic_exception(self, mock_smtp_cls):
        """Generic exceptions are caught and returned as error."""
        mock_smtp_cls.side_effect = OSError("Network unreachable")

        d = EmailNotifyDriver()
        cfg = _base_config(to_addresses=["r@example.local"])
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertFalse(result["success"])
        self.assertIn("send email", result["error"])

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_custom_port_and_timeout(self, mock_smtp_cls):
        """Custom smtp_port and timeout are passed to SMTP constructor."""
        mock_server = self._mock_smtp()
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(
            smtp_port=2525,
            timeout=10,
            to_addresses=["r@example.local"],
        )
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            result = d.send(msg, cfg)

        self.assertTrue(result["success"])
        mock_smtp_cls.assert_called_once_with("smtp.example.local", 2525, timeout=10)

    @patch("apps.notify.drivers.email.smtplib.SMTP")
    def test_send_quit_called_on_sendmail_failure(self, mock_smtp_cls):
        """server.quit() is called even when sendmail raises (finally block)."""
        mock_server = self._mock_smtp()
        mock_server.sendmail.side_effect = smtplib.SMTPException("fail")
        mock_smtp_cls.return_value = mock_server

        d = EmailNotifyDriver()
        cfg = _base_config(to_addresses=["r@example.local"])
        msg = _msg()

        prepared = {"text": "body", "html": None, "incident": {}}
        with patch.object(d, "_prepare_notification", return_value=prepared):
            d.send(msg, cfg)

        mock_server.quit.assert_called_once()
