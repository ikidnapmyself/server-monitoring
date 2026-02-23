"""Tests for BaseNotifyDriver helpers and NotificationMessage."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from apps.notify.drivers.base import BaseNotifyDriver, NotificationMessage


class DummyDriver(BaseNotifyDriver):
    name = "dummy"

    def validate_config(self, config: dict[str, object]) -> bool:
        return True

    def send(self, message: NotificationMessage, config: dict[str, object]) -> dict[str, object]:
        return {"success": True}


def _make_msg(**kwargs):
    """Create a NotificationMessage with sensible defaults."""
    defaults = {"title": "Test Alert", "message": "Something happened", "severity": "warning"}
    defaults.update(kwargs)
    return NotificationMessage(**defaults)


class TestNotificationMessage(SimpleTestCase):
    """Tests for NotificationMessage."""

    def test_notification_message_normalization(self):
        msg = NotificationMessage(title="T", message="M", severity="CRITICAL")
        assert msg.severity == "critical"

        msg2 = NotificationMessage(title="T2", message="M2", severity="unknown")
        assert msg2.severity == "info"


class TestMessageToDict(SimpleTestCase):
    """Tests for _message_to_dict."""

    def test_message_to_dict_basic(self):
        d = DummyDriver()
        msg = NotificationMessage(title="T", message="M", severity="warning")
        dd = d._message_to_dict(msg)
        assert dd["title"] == "T"
        assert dd["severity"] == "warning"


class TestRenderMessageTemplates(SimpleTestCase):
    """Tests for _render_message_templates."""

    def test_render_message_templates_delegates_to_service(self):
        """_render_message_templates delegates to the templating service."""
        driver = DummyDriver()
        msg = _make_msg()
        fake_result = {"text": "rendered text", "html": None}

        with patch.object(
            driver._templating_service,
            "render_message_templates",
            return_value=fake_result,
        ) as mock_render:
            result = driver._render_message_templates(msg, {"template": "hello {{ title }}"})

        mock_render.assert_called_once()
        assert result == fake_result


class TestTemplateContext(SimpleTestCase):
    """Tests for _template_context."""

    def test_template_context_delegates_to_service(self):
        """_template_context delegates to the templating service."""
        driver = DummyDriver()
        msg = _make_msg()
        incident_details = {"title": "Test Alert", "severity": "warning"}
        fake_ctx = {"title": "Test Alert", "incident": incident_details}

        with patch.object(
            driver._templating_service,
            "build_template_context",
            return_value=fake_ctx,
        ) as mock_ctx:
            result = driver._template_context(msg, incident_details)

        mock_ctx.assert_called_once()
        assert result == fake_ctx


class TestComposeIncidentDetails(SimpleTestCase):
    """Tests for _compose_incident_details."""

    def test_compose_incident_details_delegates_to_service(self):
        """_compose_incident_details delegates to the templating service."""
        driver = DummyDriver()
        msg = _make_msg()
        fake_incident = {"title": "Test Alert", "cpu_count": 4}

        with patch.object(
            driver._templating_service,
            "compose_incident_details",
            return_value=fake_incident,
        ) as mock_compose:
            result = driver._compose_incident_details(msg, {})

        mock_compose.assert_called_once()
        assert result == fake_incident


class TestPrepareNotification(SimpleTestCase):
    """Tests for _prepare_notification."""

    def test_payload_template_json_dict(self):
        """payload_template that renders to a JSON dict sets payload_obj."""
        driver = DummyDriver()
        msg = _make_msg()
        json_body = json.dumps({"key": "value"})

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "plain", "html": None},
            ),
            patch(
                "apps.notify.drivers.base.render_template",
                return_value=json_body,
            ),
        ):
            result = driver._prepare_notification(msg, {"payload_template": "tmpl"})

        assert result["payload_obj"] == {"key": "value"}
        assert result["payload_raw"] == json_body

    def test_payload_template_non_json(self):
        """payload_template that renders to non-JSON keeps only payload_raw."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "plain", "html": None},
            ),
            patch(
                "apps.notify.drivers.base.render_template",
                return_value="not json at all",
            ),
        ):
            result = driver._prepare_notification(msg, {"payload_template": "tmpl"})

        assert result["payload_obj"] is None
        assert result["payload_raw"] == "not json at all"

    def test_payload_template_render_error(self):
        """Render error in payload_template is re-raised."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "plain", "html": None},
            ),
            patch(
                "apps.notify.drivers.base.render_template",
                side_effect=ValueError("bad template"),
            ),
        ):
            with pytest.raises(ValueError, match="bad template"):
                driver._prepare_notification(msg, {"payload_template": "tmpl"})

    def test_auto_detect_json_dict_in_text(self):
        """When no payload_template, text that looks like JSON dict is parsed."""
        driver = DummyDriver()
        msg = _make_msg()
        json_text = json.dumps({"auto": "detected"})

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": json_text, "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["payload_obj"] == {"auto": "detected"}
        assert result["payload_raw"] == json_text

    def test_auto_detect_json_list_in_text(self):
        """When no payload_template, text that looks like JSON list is parsed."""
        driver = DummyDriver()
        msg = _make_msg()
        json_text = json.dumps([1, 2, 3])

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": json_text, "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["payload_obj"] == [1, 2, 3]
        assert result["payload_raw"] == json_text

    def test_text_looks_like_json_but_invalid(self):
        """Text starting with '{' that is not valid JSON leaves payload_obj None."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "{not valid json", "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["payload_obj"] is None
        assert result["payload_raw"] is None
        assert result["text"] == "{not valid json"

    def test_plain_text_no_json(self):
        """Plain text that doesn't start with { or [ leaves payload_obj None."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "just plain text", "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["payload_obj"] is None
        assert result["payload_raw"] is None
        assert result["text"] == "just plain text"

    def test_none_config(self):
        """Passing None as config defaults to empty dict."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "ok", "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, None)

        assert result["text"] == "ok"

    def test_payload_template_renders_empty(self):
        """payload_template that renders to empty string doesn't set payload_raw."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "plain", "html": None},
            ),
            patch("apps.notify.drivers.base.render_template", return_value=""),
        ):
            result = driver._prepare_notification(msg, {"payload_template": "tmpl"})

        assert result["payload_raw"] is None
        assert result["payload_obj"] is None

    def test_payload_template_json_non_dict(self):
        """payload_template rendering JSON that is not a dict leaves payload_obj None."""
        driver = DummyDriver()
        msg = _make_msg()
        json_string = json.dumps("just a string")

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": "plain", "html": None},
            ),
            patch(
                "apps.notify.drivers.base.render_template",
                return_value=json_string,
            ),
        ):
            result = driver._prepare_notification(msg, {"payload_template": "tmpl"})

        # JSON string is valid JSON but not a dict, so payload_obj stays None
        assert result["payload_obj"] is None
        assert result["payload_raw"] == json_string

    def test_no_payload_template_text_is_none(self):
        """When no payload_template and rendered text is None, skip JSON detection."""
        driver = DummyDriver()
        msg = _make_msg()

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": None, "html": None},
            ),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["text"] is None
        assert result["payload_obj"] is None
        assert result["payload_raw"] is None

    def test_auto_detect_json_non_dict_non_list(self):
        """Auto-detected JSON that parses to a non-dict/list leaves payload_obj None."""
        driver = DummyDriver()
        msg = _make_msg()
        json_text = '{"key": "value"}'  # starts with {

        with (
            patch.object(
                driver._templating_service,
                "compose_incident_details",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "build_template_context",
                return_value={},
            ),
            patch.object(
                driver._templating_service,
                "render_message_templates",
                return_value={"text": json_text, "html": None},
            ),
            patch("apps.notify.drivers.base.json.loads", return_value=42),
        ):
            result = driver._prepare_notification(msg, {})

        assert result["payload_obj"] is None
        assert result["payload_raw"] is None


class TestHandleHttpError(SimpleTestCase):
    """Tests for _handle_http_error."""

    def test_handle_http_error_with_body(self):
        """_handle_http_error reads error body from fp."""
        driver = DummyDriver()
        body = b"Bad Request"
        err = urllib.error.HTTPError(
            url="http://example.com",
            code=400,
            msg="Bad Request",
            hdrs=MagicMock(),
            fp=io.BytesIO(body),
        )
        result = driver._handle_http_error(err, "TestService")

        assert result["success"] is False
        assert "400" in result["error"]
        assert "Bad Request" in result["error"]
        assert "TestService" in result["error"]

    def test_handle_http_error_without_body(self):
        """_handle_http_error falls back to str(e) when fp is None."""
        driver = DummyDriver()
        err = urllib.error.HTTPError(
            url="http://example.com",
            code=500,
            msg="Server Error",
            hdrs=MagicMock(),
            fp=None,
        )
        result = driver._handle_http_error(err, "TestService")

        assert result["success"] is False
        assert "500" in result["error"]
        assert "TestService" in result["error"]


class TestHandleUrlError(SimpleTestCase):
    """Tests for _handle_url_error."""

    def test_handle_url_error(self):
        """_handle_url_error constructs proper error response."""
        driver = DummyDriver()
        err = urllib.error.URLError(reason="Connection refused")
        result = driver._handle_url_error(err, "TestService")

        assert result["success"] is False
        assert "Connection refused" in result["error"]
        assert "TestService" in result["error"]


class TestHandleException(SimpleTestCase):
    """Tests for _handle_exception."""

    def test_handle_exception(self):
        """_handle_exception constructs proper error response."""
        driver = DummyDriver()
        err = RuntimeError("timeout")
        result = driver._handle_exception(err, "TestService", "send notification to")

        assert result["success"] is False
        assert "timeout" in result["error"]
        assert "TestService" in result["error"]
        assert "send notification to" in result["error"]
