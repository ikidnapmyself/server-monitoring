"""Tests for PagerDutyNotifyDriver — full branch coverage."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.pagerduty import PagerDutyNotifyDriver


def _make_msg(**kwargs):
    """Create a NotificationMessage with sensible defaults."""
    defaults = {"title": "Test Alert", "message": "Something happened", "severity": "critical"}
    defaults.update(kwargs)
    return NotificationMessage(**defaults)


VALID_KEY = "a" * 32


def _mock_urlopen(response_body, status_code=200):
    """Create a mock context manager for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_body.encode("utf-8")
    mock_resp.getcode.return_value = status_code
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class PagerDutyValidateConfigTests(SimpleTestCase):
    """Tests for PagerDutyNotifyDriver.validate_config."""

    def setUp(self):
        self.driver = PagerDutyNotifyDriver()

    def test_missing_key_returns_false(self):
        self.assertFalse(self.driver.validate_config({}))

    def test_short_key_returns_false(self):
        self.assertFalse(self.driver.validate_config({"integration_key": "short"}))

    def test_non_string_key_returns_false(self):
        self.assertFalse(self.driver.validate_config({"integration_key": 12345}))

    def test_valid_key_returns_true(self):
        self.assertTrue(self.driver.validate_config({"integration_key": VALID_KEY}))

    def test_key_exactly_20_chars_returns_true(self):
        self.assertTrue(self.driver.validate_config({"integration_key": "x" * 20}))


class PagerDutyBuildPayloadTests(SimpleTestCase):
    """Tests for _build_payload covering all branches."""

    def setUp(self):
        self.driver = PagerDutyNotifyDriver()

    def test_trigger_action_builds_full_payload(self):
        """Trigger event_action produces payload with payload, incident, etc."""
        msg = _make_msg(tags={"fingerprint": "fp1"})
        cfg = {"integration_key": VALID_KEY, "client": "test-client"}
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["routing_key"], VALID_KEY)
        self.assertEqual(payload["event_action"], "trigger")
        self.assertIn("payload", payload)
        self.assertIsNotNone(payload.get("incident"))
        self.assertEqual(payload["dedup_key"], "fp1")
        self.assertEqual(payload["client"], "test-client")

    def test_trigger_action_with_dedup_key_from_config(self):
        """When dedup_key is in config, it takes precedence over fingerprint."""
        msg = _make_msg(tags={"fingerprint": "fp1"})
        cfg = {"integration_key": VALID_KEY, "dedup_key": "config-dedup"}
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["dedup_key"], "config-dedup")

    def test_trigger_action_no_dedup_key(self):
        """When no dedup_key and no fingerprint, dedup_key is absent."""
        msg = _make_msg(tags={})
        cfg = {"integration_key": VALID_KEY}
        payload = self.driver._build_payload(msg, cfg)

        self.assertNotIn("dedup_key", payload)

    def test_trigger_action_with_client_url(self):
        """client_url is included when present in config."""
        msg = _make_msg()
        cfg = {
            "integration_key": VALID_KEY,
            "client_url": "https://example.com/dashboard",
        }
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["client_url"], "https://example.com/dashboard")

    def test_trigger_action_with_context_url_creates_links(self):
        """When message.context has url, links are added."""
        msg = _make_msg(context={"url": "https://example.com/details"})
        cfg = {"integration_key": VALID_KEY}
        payload = self.driver._build_payload(msg, cfg)

        self.assertIn("links", payload)
        self.assertEqual(payload["links"][0]["href"], "https://example.com/details")
        self.assertEqual(payload["links"][0]["text"], "View Details")

    def test_trigger_action_no_context_url_no_links(self):
        """When message.context has no url, links are not added."""
        msg = _make_msg(context={})
        cfg = {"integration_key": VALID_KEY}
        payload = self.driver._build_payload(msg, cfg)

        self.assertNotIn("links", payload)

    def test_acknowledge_action_skips_payload_section(self):
        """Acknowledge event_action does not include 'payload' key."""
        msg = _make_msg()
        cfg = {
            "integration_key": VALID_KEY,
            "event_action": "acknowledge",
            "dedup_key": "dedup-123",
        }
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["event_action"], "acknowledge")
        self.assertNotIn("payload", payload)
        self.assertEqual(payload["dedup_key"], "dedup-123")

    def test_resolve_action_skips_payload_section(self):
        """Resolve event_action does not include 'payload' key."""
        msg = _make_msg()
        cfg = {
            "integration_key": VALID_KEY,
            "event_action": "resolve",
            "dedup_key": "dedup-456",
        }
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["event_action"], "resolve")
        self.assertNotIn("payload", payload)

    def test_trigger_with_no_payload_obj_raises_valueerror(self):
        """When _prepare_notification has no payload_obj, ValueError is raised."""
        msg = _make_msg()
        cfg = {"integration_key": VALID_KEY}

        with patch.object(
            self.driver,
            "_prepare_notification",
            return_value={"payload_obj": None, "incident": {}},
        ):
            with self.assertRaises(ValueError) as ctx:
                self.driver._build_payload(msg, cfg)
            self.assertIn("payload template required", str(ctx.exception))

    def test_trigger_severity_mapping(self):
        """PagerDuty severity is mapped from the payload_obj severity."""
        msg = _make_msg(severity="critical")
        cfg = {"integration_key": VALID_KEY}
        payload = self.driver._build_payload(msg, cfg)

        self.assertEqual(payload["payload"]["severity"], "critical")

    def test_trigger_payload_without_severity_key(self):
        """When payload_obj has no 'severity' key, severity mapping is skipped."""
        msg = _make_msg()
        cfg = {"integration_key": VALID_KEY}

        prepared = {
            "payload_obj": {"summary": "Alert", "source": "test"},
            "incident": {"title": "T"},
        }
        with patch.object(self.driver, "_prepare_notification", return_value=prepared):
            payload = self.driver._build_payload(msg, cfg)

        # severity key should not exist in the payload section
        self.assertNotIn("severity", payload["payload"])
        self.assertEqual(payload["payload"]["summary"], "Alert")


class PagerDutySendTests(SimpleTestCase):
    """Tests for send() method covering all branches."""

    def setUp(self):
        self.driver = PagerDutyNotifyDriver()
        self.config = {"integration_key": VALID_KEY}

    def test_send_invalid_config(self):
        """Invalid config returns error without making HTTP call."""
        result = self.driver.send(_make_msg(), {})
        self.assertFalse(result["success"])
        self.assertIn("Invalid PagerDuty configuration", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_success_response(self, mock_urlopen):
        """Successful PagerDuty response returns success with dedup_key."""
        resp_body = json.dumps(
            {
                "status": "success",
                "dedup_key": "dedup-abc",
                "message": "Event processed",
            }
        )
        mock_urlopen.return_value = _mock_urlopen(resp_body)

        result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        self.assertEqual(result["message_id"], "dedup-abc")
        self.assertEqual(result["metadata"]["dedup_key"], "dedup-abc")
        self.assertEqual(result["metadata"]["event_action"], "trigger")

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_error_response(self, mock_urlopen):
        """Non-success status in response body returns error."""
        resp_body = json.dumps(
            {
                "status": "invalid event",
                "message": "Event object is invalid",
            }
        )
        mock_urlopen.return_value = _mock_urlopen(resp_body)

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("PagerDuty error", result["error"])
        self.assertIn("Event object is invalid", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_error_response_unknown_message(self, mock_urlopen):
        """Non-success status with no message field defaults to 'Unknown error'."""
        resp_body = json.dumps({"status": "error"})
        mock_urlopen.return_value = _mock_urlopen(resp_body)

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("Unknown error", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_http_error_with_json_body(self, mock_urlopen):
        """HTTPError with JSON body extracts message."""
        error_body = io.BytesIO(json.dumps({"message": "Invalid routing key"}).encode())
        http_error = urllib.error.HTTPError(
            url="https://events.pagerduty.com/v2/enqueue",
            code=400,
            msg="Bad Request",
            hdrs=MagicMock(),
            fp=error_body,
        )
        mock_urlopen.side_effect = http_error

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("400", result["error"])
        self.assertIn("Invalid routing key", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_http_error_with_non_json_body(self, mock_urlopen):
        """HTTPError with non-JSON body uses raw text."""
        error_body = io.BytesIO(b"Service Unavailable")
        http_error = urllib.error.HTTPError(
            url="https://events.pagerduty.com/v2/enqueue",
            code=503,
            msg="Service Unavailable",
            hdrs=MagicMock(),
            fp=error_body,
        )
        mock_urlopen.side_effect = http_error

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("503", result["error"])
        self.assertIn("Service Unavailable", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_http_error_no_fp(self, mock_urlopen):
        """HTTPError with fp=None uses str(e)."""
        http_error = urllib.error.HTTPError(
            url="https://events.pagerduty.com/v2/enqueue",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=None,
        )
        mock_urlopen.side_effect = http_error

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("500", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_url_error(self, mock_urlopen):
        """URLError is handled via _handle_url_error."""
        mock_urlopen.side_effect = urllib.error.URLError("DNS resolution failed")

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("Failed to connect to PagerDuty", result["error"])

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_generic_exception(self, mock_urlopen):
        """Generic Exception is handled via _handle_exception."""
        mock_urlopen.side_effect = RuntimeError("something broke")

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("something broke", result["error"])

    @patch("apps.notify.drivers.pagerduty.logger")
    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_send_http_error_logs_error(self, mock_urlopen, mock_logger):
        """HTTPError is logged before returning the error response."""
        error_body = io.BytesIO(json.dumps({"message": "Forbidden"}).encode())
        http_error = urllib.error.HTTPError(
            url="https://events.pagerduty.com/v2/enqueue",
            code=403,
            msg="Forbidden",
            hdrs=MagicMock(),
            fp=error_body,
        )
        mock_urlopen.side_effect = http_error

        result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        mock_logger.error.assert_called_once()
        logged_msg = mock_logger.error.call_args[0][0]
        self.assertIn("403", logged_msg)


class PagerDutyAcknowledgeResolveTests(SimpleTestCase):
    """Tests for acknowledge() and resolve() convenience methods."""

    def setUp(self):
        self.driver = PagerDutyNotifyDriver()
        self.config = {"integration_key": VALID_KEY}

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_acknowledge_sends_acknowledge_action(self, mock_urlopen):
        """acknowledge() calls send with event_action=acknowledge."""
        resp_body = json.dumps(
            {
                "status": "success",
                "dedup_key": "dedup-ack",
                "message": "Event processed",
            }
        )
        mock_urlopen.return_value = _mock_urlopen(resp_body)

        result = self.driver.acknowledge("dedup-ack", self.config)

        self.assertTrue(result["success"])
        # Verify the request payload had acknowledge action
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["event_action"], "acknowledge")
        self.assertEqual(sent_data["dedup_key"], "dedup-ack")

    @patch("apps.notify.drivers.pagerduty.urllib.request.urlopen")
    def test_resolve_sends_resolve_action(self, mock_urlopen):
        """resolve() calls send with event_action=resolve."""
        resp_body = json.dumps(
            {
                "status": "success",
                "dedup_key": "dedup-res",
                "message": "Event processed",
            }
        )
        mock_urlopen.return_value = _mock_urlopen(resp_body)

        result = self.driver.resolve("dedup-res", self.config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode("utf-8"))
        self.assertEqual(sent_data["event_action"], "resolve")
        self.assertEqual(sent_data["dedup_key"], "dedup-res")
