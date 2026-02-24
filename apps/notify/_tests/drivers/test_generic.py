"""Tests for GenericNotifyDriver — full branch coverage."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.notify.drivers.base import NotificationMessage
from apps.notify.drivers.generic import GenericNotifyDriver


def _make_msg(**kwargs):
    """Create a NotificationMessage with sensible defaults."""
    defaults = {"title": "Test Alert", "message": "Something happened", "severity": "info"}
    defaults.update(kwargs)
    return NotificationMessage(**defaults)


ENDPOINT = "https://example.com/webhook"


def _mock_urlopen(response_body, status_code=200):
    """Create a mock context manager for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_body.encode("utf-8")
    mock_resp.getcode.return_value = status_code
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class GenericValidateConfigTests(SimpleTestCase):
    """Tests for GenericNotifyDriver.validate_config — all branches."""

    def setUp(self):
        self.driver = GenericNotifyDriver()

    def test_empty_config_returns_true(self):
        """Empty config means notifications disabled (no-op)."""
        self.assertTrue(self.driver.validate_config({}))

    def test_none_config_returns_true(self):
        """None config means notifications disabled."""
        self.assertTrue(self.driver.validate_config(None))

    def test_disabled_flag_returns_true(self):
        """Config with disabled=True returns True (no-op)."""
        self.assertTrue(self.driver.validate_config({"disabled": True}))

    def test_missing_endpoint_and_webhook_url_returns_false(self):
        """Config with no endpoint or webhook_url returns False."""
        self.assertFalse(self.driver.validate_config({"some_key": "val"}))

    def test_invalid_url_returns_false(self):
        """Non-http(s) URL returns False."""
        self.assertFalse(self.driver.validate_config({"endpoint": "ftp://bad.com/hook"}))

    def test_valid_endpoint_returns_true(self):
        """Valid https endpoint returns True."""
        self.assertTrue(self.driver.validate_config({"endpoint": ENDPOINT}))

    def test_valid_webhook_url_returns_true(self):
        """Valid webhook_url returns True."""
        self.assertTrue(self.driver.validate_config({"webhook_url": ENDPOINT}))

    def test_http_endpoint_returns_true(self):
        """http:// URLs are also accepted."""
        self.assertTrue(self.driver.validate_config({"endpoint": "http://localhost:8080/hook"}))


class GenericBuildPayloadTests(SimpleTestCase):
    """Tests for _build_payload covering all branches."""

    def setUp(self):
        self.driver = GenericNotifyDriver()

    def test_build_payload_from_payload_obj(self):
        """When _prepare_notification returns payload_obj (dict), it is used directly."""
        msg = _make_msg()
        prepared = {
            "payload_obj": {"title": "T", "message": "M"},
            "payload_raw": '{"title": "T", "message": "M"}',
            "incident": {"title": "T"},
            "text": None,
            "html": None,
        }
        with patch.object(self.driver, "_prepare_notification", return_value=prepared):
            payload = self.driver._build_payload(msg, {"endpoint": ENDPOINT})

        self.assertEqual(payload["title"], "T")
        self.assertIn("incident", payload)

    def test_build_payload_from_payload_raw(self):
        """When payload_obj is None but payload_raw exists, wraps in title/message/incident."""
        msg = _make_msg()
        prepared = {
            "payload_obj": None,
            "payload_raw": "plain text notification",
            "incident": {"title": "T"},
            "text": None,
            "html": None,
        }
        with patch.object(self.driver, "_prepare_notification", return_value=prepared):
            payload = self.driver._build_payload(msg, {"endpoint": ENDPOINT})

        self.assertEqual(payload["title"], "Test Alert")
        self.assertEqual(payload["message"], "plain text notification")
        self.assertIn("incident", payload)

    def test_build_payload_from_non_dict_payload_obj(self):
        """When payload_obj is a list (not dict), skip setdefault and return as-is."""
        msg = _make_msg()
        prepared = {
            "payload_obj": [{"item": 1}, {"item": 2}],
            "payload_raw": '[{"item": 1}, {"item": 2}]',
            "incident": {"title": "T"},
            "text": None,
            "html": None,
        }
        with patch.object(self.driver, "_prepare_notification", return_value=prepared):
            payload = self.driver._build_payload(msg, {"endpoint": ENDPOINT})

        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 2)

    def test_build_payload_no_payload_raises_valueerror(self):
        """When neither payload_obj nor payload_raw exist, raises ValueError."""
        msg = _make_msg()
        prepared = {
            "payload_obj": None,
            "payload_raw": None,
            "incident": {},
            "text": None,
            "html": None,
        }
        with patch.object(self.driver, "_prepare_notification", return_value=prepared):
            with self.assertRaises(ValueError) as ctx:
                self.driver._build_payload(msg, {"endpoint": ENDPOINT})
            self.assertIn("payload template required", str(ctx.exception))

    def test_build_payload_with_template(self):
        """Integration test using actual file:generic_payload.j2 template."""
        msg = _make_msg()
        cfg = {"endpoint": ENDPOINT, "payload_template": "file:generic_payload.j2"}
        payload = self.driver._build_payload(msg, cfg)
        self.assertIn("title", payload)
        self.assertEqual(payload["title"], "Test Alert")


class GenericSendTests(SimpleTestCase):
    """Tests for send() method covering all branches."""

    def setUp(self):
        self.driver = GenericNotifyDriver()
        self.config = {"endpoint": ENDPOINT}

    def test_send_invalid_config(self):
        """Invalid config returns error without making HTTP call."""
        result = self.driver.send(_make_msg(), {"endpoint": "not-a-url"})
        self.assertFalse(result["success"])
        self.assertIn("Invalid configuration", result["error"])

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_post_with_json_response(self, mock_urlopen):
        """Successful POST with JSON response body."""
        resp = json.dumps({"status": "received"})
        mock_urlopen.return_value = _mock_urlopen(resp)

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T", "message": "M"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        self.assertIn("message_id", result)
        self.assertEqual(result["metadata"]["method"], "POST")
        self.assertEqual(result["metadata"]["endpoint"], ENDPOINT)
        self.assertEqual(result["metadata"]["response"]["status"], "received")

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_post_with_non_json_response(self, mock_urlopen):
        """Successful POST with non-JSON response body wraps in raw key."""
        mock_urlopen.return_value = _mock_urlopen("OK")

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["response"]["raw"], "OK")

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_get_method_no_body(self, mock_urlopen):
        """GET method does not send request body (data=None)."""
        mock_urlopen.return_value = _mock_urlopen("{}")

        config = {"endpoint": ENDPOINT, "method": "GET"}
        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        self.assertIsNone(request_obj.data)
        self.assertEqual(request_obj.method, "GET")

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_put_method_sends_body(self, mock_urlopen):
        """PUT method sends request body."""
        mock_urlopen.return_value = _mock_urlopen("{}")

        config = {"endpoint": ENDPOINT, "method": "PUT"}
        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        self.assertIsNotNone(request_obj.data)
        self.assertEqual(request_obj.method, "PUT")

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_custom_headers(self, mock_urlopen):
        """Custom headers from config are merged with defaults."""
        mock_urlopen.return_value = _mock_urlopen("{}")

        config = {
            "endpoint": ENDPOINT,
            "headers": {"Authorization": "Bearer token123", "X-Custom": "value"},
        }
        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), config)

        self.assertTrue(result["success"])
        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        self.assertEqual(request_obj.get_header("Authorization"), "Bearer token123")
        self.assertEqual(request_obj.get_header("X-custom"), "value")
        # Default headers should still be present
        self.assertEqual(request_obj.get_header("Content-type"), "application/json")

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_uses_webhook_url_fallback(self, mock_urlopen):
        """When endpoint is missing, webhook_url is used."""
        mock_urlopen.return_value = _mock_urlopen("{}")
        alt_url = "https://alt.example.com/hook"

        config = {"webhook_url": alt_url}
        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), config)

        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["endpoint"], alt_url)

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_http_error(self, mock_urlopen):
        """HTTPError is handled with custom error message."""
        error_body = io.BytesIO(b"Bad Request: invalid payload")
        http_error = urllib.error.HTTPError(
            url=ENDPOINT,
            code=400,
            msg="Bad Request",
            hdrs=MagicMock(),
            fp=error_body,
        )
        mock_urlopen.side_effect = http_error

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("400", result["error"])
        self.assertIn("Bad Request: invalid payload", result["error"])

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_http_error_no_fp(self, mock_urlopen):
        """HTTPError with fp=None uses str(e) for error body."""
        http_error = urllib.error.HTTPError(
            url=ENDPOINT,
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=None,
        )
        mock_urlopen.side_effect = http_error

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("500", result["error"])

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_url_error(self, mock_urlopen):
        """URLError is handled via _handle_url_error."""
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("Failed to connect to Generic", result["error"])

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_send_generic_exception(self, mock_urlopen):
        """Generic Exception is handled via _handle_exception."""
        mock_urlopen.side_effect = RuntimeError("unexpected error")

        with patch.object(
            self.driver,
            "_build_payload",
            return_value={"title": "T"},
        ):
            result = self.driver.send(_make_msg(), self.config)

        self.assertFalse(result["success"])
        self.assertIn("unexpected error", result["error"])


class GenericSendDisabledTests(SimpleTestCase):
    """Tests for send() no-op behaviour when config is empty or disabled."""

    def setUp(self):
        self.driver = GenericNotifyDriver()

    def test_send_empty_config_returns_noop_success(self):
        """Empty config means notifications are disabled; send() is a no-op."""
        result = self.driver.send(_make_msg(), {})
        self.assertTrue(result["success"])
        self.assertIsNone(result["message_id"])
        self.assertTrue(result["metadata"]["disabled"])

    def test_send_none_config_returns_noop_success(self):
        """None config means notifications are disabled; send() is a no-op."""
        result = self.driver.send(_make_msg(), None)
        self.assertTrue(result["success"])
        self.assertTrue(result["metadata"]["disabled"])

    def test_send_disabled_flag_returns_noop_success(self):
        """Config with disabled=True is a no-op."""
        result = self.driver.send(_make_msg(), {"disabled": True})
        self.assertTrue(result["success"])
        self.assertTrue(result["metadata"]["disabled"])


class GenericUserAgentTests(SimpleTestCase):
    """Tests that the correct User-Agent header is sent."""

    @patch("apps.notify.drivers.generic.urllib.request.urlopen")
    def test_user_agent_header_is_server_monitoring(self, mock_urlopen):
        """User-Agent header must be 'ServerMonitoring/1.0'."""
        mock_urlopen.return_value = _mock_urlopen("{}")
        driver = GenericNotifyDriver()
        config = {"endpoint": ENDPOINT}

        with patch.object(driver, "_build_payload", return_value={"title": "T"}):
            driver.send(_make_msg(), config)

        call_args = mock_urlopen.call_args
        request_obj = call_args[0][0]
        self.assertEqual(request_obj.get_header("User-agent"), "ServerMonitoring/1.0")
