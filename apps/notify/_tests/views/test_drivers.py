from django.test import Client, TestCase, override_settings


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
