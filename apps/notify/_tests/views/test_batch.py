import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings


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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
            "apps.notify.views.batch.DRIVER_REGISTRY",
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
