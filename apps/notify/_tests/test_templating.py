"""Tests for templating utilities."""

from django.test import SimpleTestCase

from apps.notify.templating import NotificationTemplatingService, render_template


class TemplatingTests(SimpleTestCase):
    def test_render_inline_template(self):
        out = render_template("Hello {{ name }}", {"name": "World"})
        self.assertIn("Hello", out)
        self.assertIn("World", out)

    def test_render_file_template_exists(self):
        # Use a known template present in the repo (email_text.j2)
        out = render_template(
            "file:email_text.j2",
            {
                "title": "X",
                "message": "M",
                "severity": "info",
                "channel": "c",
                "tags": {},
                "context": {},
                "incident": {},
            },
        )
        self.assertIsNotNone(out)

    def test_notification_templating_service_builds_context(self):
        svc = NotificationTemplatingService()
        msg = {
            "title": "T",
            "message": "M",
            "severity": "info",
            "channel": "c",
            "tags": {},
            "context": {"incident_id": 5},
        }
        incident = svc.compose_incident_details(msg, {})
        ctx = svc.build_template_context(msg, incident)
        self.assertEqual(ctx["title"], "T")
        self.assertIn("incident_id", ctx)
