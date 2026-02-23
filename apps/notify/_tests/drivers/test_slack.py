"""Tests for SlackNotifyDriver — full branch coverage."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.slack import SlackNotifyDriver


def _make_msg(**kwargs):
    """Create a NotificationMessage with sensible defaults."""
    defaults = {"title": "Test Alert", "message": "Something happened", "severity": "warning"}
    defaults.update(kwargs)
    return NotificationMessage(**defaults)


VALID_WEBHOOK = "https://hooks.slack.com/services/T00/B00/xxx"


class SlackValidateConfigTests(SimpleTestCase):
    """Tests for SlackNotifyDriver.validate_config."""

    def setUp(self):
        self.driver = SlackNotifyDriver()

    def test_missing_webhook_url_returns_false(self):
        self.assertFalse(self.driver.validate_config({}))

    def test_non_string_webhook_url_returns_false(self):
        self.assertFalse(self.driver.validate_config({"webhook_url": 12345}))

    def test_wrong_prefix_returns_false(self):
        self.assertFalse(self.driver.validate_config({"webhook_url": "https://example.com/hook"}))

    def test_valid_config_returns_true(self):
        self.assertTrue(self.driver.validate_config({"webhook_url": VALID_WEBHOOK}))


class SlackSendInvalidConfigTests(SimpleTestCase):
    """Tests for send() with invalid config — early return."""

    def test_send_invalid_config_returns_error(self):
        driver = SlackNotifyDriver()
        msg = _make_msg()
        result = driver.send(msg, {})
        self.assertFalse(result["success"])
        self.assertIn("Invalid Slack configuration", result["error"])


class SlackSendPayloadBranchTests(SimpleTestCase):
    """Tests for send() covering JSON dict, JSON list, and plain text payload branches."""

    def setUp(self):
        self.driver = SlackNotifyDriver()
        self.config = {"webhook_url": VALID_WEBHOOK}

    def _mock_urlopen(self, response_body="ok", status_code=200):
        """Create a mock for urllib.request.urlopen returning the given body."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body.encode("utf-8")
        mock_resp.getcode.return_value = status_code
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_json_dict_payload(self, mock_urlopen):
        """When template renders valid JSON dict, it becomes the payload directly."""
        mock_urlopen.return_value = self._mock_urlopen("ok")
        payload_dict = {"text": "Hello from template", "channel": "#alerts"}

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": json.dumps(payload_dict), "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        # Verify the request was made with the dict payload
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["text"], "Hello from template")
        self.assertEqual(sent_data["channel"], "#alerts")

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_json_list_payload(self, mock_urlopen):
        """When template renders a JSON list, it becomes blocks."""
        mock_urlopen.return_value = self._mock_urlopen("ok")
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": json.dumps(blocks), "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["blocks"], blocks)

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_plain_text_payload(self, mock_urlopen):
        """When template renders plain text, it becomes {"text": ...}."""
        mock_urlopen.return_value = self._mock_urlopen("ok")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "Just a plain text message", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["text"], "Just a plain text message")

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_invalid_json_starting_with_brace(self, mock_urlopen):
        """When template renders text starting with { but not valid JSON, falls to plain text."""
        mock_urlopen.return_value = self._mock_urlopen("ok")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "{not valid json at all", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        # Falls through to payload_raw -> {"text": payload_raw}
        self.assertEqual(sent_data["text"], "{not valid json at all")


class SlackSendResponseTests(SimpleTestCase):
    """Tests for send() response handling branches."""

    def setUp(self):
        self.driver = SlackNotifyDriver()
        self.config = {"webhook_url": VALID_WEBHOOK}

    def _mock_urlopen(self, response_body="ok"):
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_ok_response(self, mock_urlopen):
        """When Slack responds with 'ok', return success."""
        mock_urlopen.return_value = self._mock_urlopen("ok")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        self.assertIn("message_id", result)
        self.assertIn("metadata", result)
        self.assertEqual(result["metadata"]["severity"], "warning")

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_non_ok_response(self, mock_urlopen):
        """When Slack responds with something other than 'ok', return failure."""
        mock_urlopen.return_value = self._mock_urlopen("invalid_token")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("Unexpected Slack response", result["error"])

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_empty_rendered_text_raises_valueerror(self, mock_urlopen):
        """When _render_message_templates returns no text, send catches the ValueError."""
        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": None, "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        # The ValueError is caught by the generic Exception handler
        self.assertFalse(result["success"])
        self.assertIn("template required", result["error"])


class SlackSendErrorHandlerTests(SimpleTestCase):
    """Tests for send() error handler branches."""

    def setUp(self):
        self.driver = SlackNotifyDriver()
        self.config = {"webhook_url": VALID_WEBHOOK}

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_http_error(self, mock_urlopen):
        """HTTPError is handled via _handle_http_error."""
        error_body = io.BytesIO(b"rate_limited")
        http_error = urllib.error.HTTPError(
            url=VALID_WEBHOOK,
            code=429,
            msg="Too Many Requests",
            hdrs=MagicMock(),
            fp=error_body,
        )
        mock_urlopen.side_effect = http_error

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("429", result["error"])
        self.assertIn("rate_limited", result["error"])

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_url_error(self, mock_urlopen):
        """URLError is handled via _handle_url_error."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("Failed to connect to Slack", result["error"])

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_generic_exception(self, mock_urlopen):
        """Generic Exception is handled via _handle_exception."""
        mock_urlopen.side_effect = RuntimeError("unexpected failure")

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("unexpected failure", result["error"])

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_with_custom_timeout(self, mock_urlopen):
        """Custom timeout from config is passed through."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        config = {"webhook_url": VALID_WEBHOOK, "timeout": 10}
        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "hello", "html": None},
        ):
            result = self.driver.send(_make_msg(), config)

        self.assertTrue(result["success"])
        # Verify timeout was passed
        call_kwargs = mock_urlopen.call_args
        self.assertEqual(call_kwargs[1]["timeout"], 10)

    @patch("apps.notify.drivers.slack.urllib.request.urlopen")
    def test_send_invalid_json_starting_with_bracket(self, mock_urlopen):
        """Text starting with [ but invalid JSON falls through to plain text."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"ok"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.object(
            self.driver,
            "_render_message_templates",
            return_value={"text": "[not valid json", "html": None},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["text"], "[not valid json")
