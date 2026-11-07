import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings


@override_settings(API_KEY_AUTH_ENABLED=False)
class NotifyViewPostTest(TestCase):
    """Tests for NotifyView.post (POST /notify/send/)."""

    def setUp(self):
        self.client = Client()

    def test_invalid_json(self):
        response = self.client.post(
            "/notify/send/",
            data="not json{{{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid JSON", data["message"])

    def test_missing_required_fields_no_title(self):
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"message": "Hello"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required fields", response.json()["message"])

    def test_missing_required_fields_no_message(self):
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "Hello"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required fields", response.json()["message"])

    @patch("apps.notify.services.NotifySelector")
    def test_unknown_driver(self, mock_selector):
        mock_selector.resolve.return_value = (
            "bogus",
            {},
            "bogus",
            None,  # driver_class is None
            None,
            "default",
        )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M", "driver": "bogus"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Unknown driver/provider", data["message"])
        self.assertIn("available_drivers", data)

    @patch("apps.notify.services.NotifySelector")
    def test_invalid_config(self, mock_selector):
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = False

        mock_selector.resolve.return_value = (
            "generic",
            {},
            "generic",
            mock_driver_class,
            None,
            "default",
        )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid configuration", response.json()["message"])

    @patch("apps.notify.services.NotifySelector")
    def test_successful_send(self, mock_selector):
        mock_driver_class = MagicMock()
        mock_driver_instance = mock_driver_class.return_value
        mock_driver_instance.validate_config.return_value = True
        mock_driver_instance.send.return_value = {
            "success": True,
            "message_id": "abc123",
            "metadata": {"ts": "1"},
        }

        mock_selector.resolve.return_value = (
            "generic",
            {},
            "generic",
            mock_driver_class,
            None,
            "default",
        )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["driver"], "generic")
        self.assertEqual(data["message_id"], "abc123")

    @patch("apps.notify.services.NotifySelector")
    def test_failed_send(self, mock_selector):
        mock_driver_class = MagicMock()
        mock_driver_instance = mock_driver_class.return_value
        mock_driver_instance.validate_config.return_value = True
        mock_driver_instance.send.return_value = {
            "success": False,
            "error": "timeout",
        }

        mock_selector.resolve.return_value = (
            "generic",
            {},
            "generic",
            mock_driver_class,
            None,
            "default",
        )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["message"], "timeout")

    @patch("apps.notify.services.NotifySelector")
    def test_unexpected_exception(self, mock_selector):
        mock_selector.resolve.side_effect = RuntimeError("boom")

        response = self.client.post(
            "/notify/send/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("boom", data["message"])

    @patch("apps.notify.services.NotifySelector")
    def test_post_with_url_driver(self, mock_selector):
        """POST /notify/send/<driver>/ — driver comes from URL."""
        mock_driver_class = MagicMock()
        mock_driver_instance = mock_driver_class.return_value
        mock_driver_instance.validate_config.return_value = True
        mock_driver_instance.send.return_value = {"success": True, "message_id": "x"}

        mock_selector.resolve.return_value = (
            "slack",
            {},
            "slack",
            mock_driver_class,
            None,
            "default",
        )
        response = self.client.post(
            "/notify/send/slack/",
            data=json.dumps({"title": "T", "message": "M"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # Verify resolve was called with the URL driver
        mock_selector.resolve.assert_called_once()
        args = mock_selector.resolve.call_args[0]
        self.assertEqual(args[0], "slack")

    @patch("apps.notify.services.NotifySelector")
    def test_post_with_all_optional_fields(self, mock_selector):
        """Covers severity, channel, tags, context from payload."""
        mock_driver_class = MagicMock()
        mock_driver_instance = mock_driver_class.return_value
        mock_driver_instance.validate_config.return_value = True
        mock_driver_instance.send.return_value = {"success": True}

        mock_selector.resolve.return_value = (
            "generic",
            {"key": "val"},
            "my-label",
            mock_driver_class,
            None,
            "ops-channel",
        )
        response = self.client.post(
            "/notify/send/",
            data=json.dumps(
                {
                    "title": "T",
                    "message": "M",
                    "severity": "critical",
                    "channel": "ops-team",
                    "tags": {"env": "prod"},
                    "context": {"cpu": 95},
                    "config": {"key": "val"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)


@override_settings(API_KEY_AUTH_ENABLED=False)
class NotifyViewGetTest(TestCase):
    """Tests for NotifyView.get (GET /notify/send/)."""

    def setUp(self):
        self.client = Client()

    def test_health_check(self):
        response = self.client.get("/notify/send/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("available_drivers", data)

    def test_known_driver(self):
        response = self.client.get("/notify/send/generic/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["driver"], "generic")

    def test_unknown_driver(self):
        response = self.client.get("/notify/send/bogus/")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Unknown driver", data["message"])
        self.assertIn("available_drivers", data)
