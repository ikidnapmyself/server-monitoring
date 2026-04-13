"""Tests for apps.notify.views — targeting 100% branch coverage."""

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


@override_settings(API_KEY_AUTH_ENABLED=False)
class NotifyBatchViewPostTest(TestCase):
    """Tests for NotifyBatchView.post (POST /notify/batch/)."""

    def setUp(self):
        self.client = Client()

    def test_invalid_json(self):
        response = self.client.post(
            "/notify/batch/",
            data="not json{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["message"])

    def test_empty_notifications(self):
        response = self.client.post(
            "/notify/batch/",
            data=json.dumps({"notifications": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No notifications", response.json()["message"])

    def test_missing_notifications_key(self):
        response = self.client.post(
            "/notify/batch/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No notifications", response.json()["message"])

    def test_missing_fields_in_batch_item(self):
        mock_driver_class = MagicMock()
        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps({"notifications": [{"title": "T"}]}),  # missing message
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error_count"], 1)
        self.assertFalse(data["results"][0]["success"])

    def test_unknown_driver_in_batch(self):
        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": MagicMock()},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {
                        "notifications": [
                            {
                                "driver": "nope",
                                "title": "T",
                                "message": "M",
                            }
                        ]
                    }
                ),
                content_type="application/json",
            )
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Unknown driver", data["results"][0]["error"])

    def test_invalid_config_in_batch(self):
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = False

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {"notifications": [{"driver": "generic", "title": "T", "message": "M"}]}
                ),
                content_type="application/json",
            )
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Invalid configuration", data["results"][0]["error"])

    def test_successful_batch_item(self):
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = True
        mock_driver_class.return_value.send.return_value = {
            "success": True,
            "message_id": "batch-1",
        }

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {"notifications": [{"driver": "generic", "title": "T", "message": "M"}]}
                ),
                content_type="application/json",
            )
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["success_count"], 1)
        self.assertTrue(data["results"][0]["success"])
        self.assertEqual(data["results"][0]["message_id"], "batch-1")

    def test_failed_batch_item(self):
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = True
        mock_driver_class.return_value.send.return_value = {
            "success": False,
            "error": "send failed",
        }

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {"notifications": [{"driver": "generic", "title": "T", "message": "M"}]}
                ),
                content_type="application/json",
            )
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error_count"], 1)
        self.assertFalse(data["results"][0]["success"])

    def test_partial_status(self):
        """Mix of success and failure yields 'partial' status."""
        mock_driver_class = MagicMock()
        instance = mock_driver_class.return_value
        instance.validate_config.return_value = True
        # First call succeeds, second fails
        instance.send.side_effect = [
            {"success": True, "message_id": "ok"},
            {"success": False, "error": "fail"},
        ]

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {
                        "notifications": [
                            {"driver": "generic", "title": "T1", "message": "M1"},
                            {"driver": "generic", "title": "T2", "message": "M2"},
                        ]
                    }
                ),
                content_type="application/json",
            )
        data = response.json()
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["success_count"], 1)
        self.assertEqual(data["error_count"], 1)

    def test_unexpected_exception(self):
        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": MagicMock(side_effect=RuntimeError("kaboom"))},
            clear=True,
        ):
            # The exception happens inside the try block when instantiating
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {"notifications": [{"driver": "generic", "title": "T", "message": "M"}]}
                ),
                content_type="application/json",
            )
        # The outer try/except catches RuntimeError
        self.assertEqual(response.status_code, 500)
        self.assertIn("kaboom", response.json()["message"])

    def test_batch_with_optional_fields(self):
        """Cover severity, channel, tags, context in batch items."""
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = True
        mock_driver_class.return_value.send.return_value = {"success": True}

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {
                        "notifications": [
                            {
                                "driver": "generic",
                                "title": "T",
                                "message": "M",
                                "severity": "critical",
                                "channel": "ops",
                                "tags": {"env": "prod"},
                                "context": {"cpu": 99},
                                "config": {"endpoint": "http://x"},
                            }
                        ]
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

    def test_default_driver_when_not_specified(self):
        """When 'driver' key is missing, defaults to 'generic'."""
        mock_driver_class = MagicMock()
        mock_driver_class.return_value.validate_config.return_value = True
        mock_driver_class.return_value.send.return_value = {"success": True}

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY",
            {"generic": mock_driver_class},
            clear=True,
        ):
            response = self.client.post(
                "/notify/batch/",
                data=json.dumps(
                    {"notifications": [{"title": "T", "message": "M"}]}  # no driver key
                ),
                content_type="application/json",
            )
        self.assertEqual(response.json()["status"], "success")


@override_settings(API_KEY_AUTH_ENABLED=False)
class NotifyBatchViewGetTest(TestCase):
    """Tests for NotifyBatchView.get (GET /notify/batch/)."""

    def setUp(self):
        self.client = Client()

    def test_health_check(self):
        response = self.client.get("/notify/batch/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("available_drivers", data)


@override_settings(API_KEY_AUTH_ENABLED=False)
class DriversViewTest(TestCase):
    """Tests for DriversView.get (GET /notify/drivers/)."""

    def setUp(self):
        self.client = Client()

    def test_list_all_drivers(self):
        response = self.client.get("/notify/drivers/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIsInstance(data["drivers"], list)
        self.assertTrue(len(data["drivers"]) > 0)

    def test_specific_driver(self):
        response = self.client.get("/notify/drivers/email/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["driver"]["name"], "email")

    def test_unknown_driver(self):
        response = self.client.get("/notify/drivers/bogus/")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Unknown driver", data["message"])
        self.assertIn("available_drivers", data)
