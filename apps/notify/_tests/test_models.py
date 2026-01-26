from django.test import TestCase

from apps.notify.models import NotificationChannel, NotificationSeverity


class NotificationChannelTests(TestCase):
    def test_notification_channel_crud(self):
        ch = NotificationChannel.objects.create(name="test-channel", driver="generic", config={})
        self.assertIsNotNone(ch.pk)
        self.assertTrue(ch.is_active)
        self.assertTrue(str(ch).startswith("test-channel"))


class NotificationSeverityTests(TestCase):
    def test_notification_severity_choices(self):
        vals = [c[0] for c in NotificationSeverity.choices]
        self.assertIn("critical", vals)
        self.assertIn("info", vals)
