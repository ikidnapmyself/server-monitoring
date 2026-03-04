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


class EmailHtmlTemplateTests(SimpleTestCase):
    """Tests for email_html.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops@example.com",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "ingest": "raw ingest data",
                "check": "raw check data",
                "generated_at": "2026-03-03T12:00:00+00:00",
                "environment": "production",
                "cpu_count": 4,
                "ram_total_human": "16.0 GB",
                "disk_total_human": "500.0 GB",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker", "Increase memory limit"],
            },
            "recommendations": [
                {
                    "title": "Restart",
                    "description": "Restart the worker process",
                    "priority": "high",
                },
                {
                    "title": "Scale up",
                    "description": "Add more RAM",
                    "priority": "medium",
                },
            ],
        }
        ctx.update(overrides)
        return render_template("file:email_html.j2", ctx)

    def test_html_escapes_title(self):
        out = self._render(title="<script>alert('xss')</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_html_escapes_message(self):
        out = self._render(message="<img src=x onerror=alert(1)>")
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;img", out)

    def test_html_escapes_intelligence_summary(self):
        out = self._render(
            intelligence={
                "summary": "<b>bold</b>",
                "probable_cause": None,
                "actions": None,
            }
        )
        self.assertNotIn("<b>bold</b>", out)
        self.assertIn("&lt;b&gt;", out)

    def test_includes_incident_id(self):
        out = self._render()
        self.assertIn("42", out)

    def test_includes_source(self):
        out = self._render()
        self.assertIn("local-monitor", out)

    def test_includes_generated_at(self):
        out = self._render()
        self.assertIn("2026-03-03T12:00:00", out)

    def test_includes_probable_cause(self):
        out = self._render()
        self.assertIn("Memory leak in worker", out)

    def test_includes_intelligence_actions(self):
        out = self._render()
        self.assertIn("Restart worker", out)
        self.assertIn("Increase memory limit", out)

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        self.assertIn("Rec 9", out)
        self.assertNotIn("Rec 10", out)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)
        self.assertIn("CPU usage at 95%", out)
