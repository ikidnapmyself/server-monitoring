"""Tests for templating utilities."""

import json

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


class EmailTextTemplateTests(SimpleTestCase):
    """Tests for email_text.j2 template normalization."""

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
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker", "Increase memory limit"],
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker process"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:email_text.j2", ctx)

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

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)


class SlackTextTemplateTests(SimpleTestCase):
    """Tests for slack_text.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "#ops",
            "tags": {},
            "context": {},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {},
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": None,
                "actions": None,
            },
            "recommendations": None,
        }
        ctx.update(overrides)
        return render_template("file:slack_text.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("blocks", data)
        self.assertIn("text", data)

    def test_includes_incident_id_in_header(self):
        out = self._render()
        data = json.loads(out)
        header_text = data["blocks"][0]["text"]["text"]
        self.assertIn("42", header_text)

    def test_includes_source_in_header(self):
        out = self._render()
        data = json.loads(out)
        header_text = data["blocks"][0]["text"]["text"]
        self.assertIn("local-monitor", header_text)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
        )
        data = json.loads(out)
        self.assertIn("blocks", data)

    def test_recommendations_capped_at_5(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(8)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        full_text = json.dumps(data)
        self.assertIn("Rec 4", full_text)
        self.assertNotIn("Rec 5", full_text)


class GenericPayloadTemplateTests(SimpleTestCase):
    """Tests for generic_payload.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "webhook-1",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "generated_at": "2026-03-03T12:00:00+00:00",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker process"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:generic_payload.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["title"], "CPU Alert")

    def test_includes_source(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["source"], "local-monitor")

    def test_includes_generated_at(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("generated_at", data)

    def test_includes_probable_cause(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["probable_cause"], "Memory leak in worker")

    def test_valid_json_with_empty_tags_and_context(self):
        out = self._render(tags={}, context={})
        data = json.loads(out)
        self.assertEqual(data["tags"], {})
        self.assertEqual(data["context"], {})

    def test_valid_json_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        data = json.loads(out)
        self.assertEqual(data["title"], "CPU Alert")

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        self.assertEqual(len(data["recommendations"]), 10)


class PagerDutyPayloadTemplateTests(SimpleTestCase):
    """Tests for pagerduty_payload.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops-pd",
            "tags": {"trace_id": "abc-123"},
            "context": {"env": "production"},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "source": "local-monitor",
                "generated_at": "2026-03-03T12:00:00+00:00",
                "cpu_count": 4,
                "ram_total_human": "16.0 GB",
                "disk_total_human": "500.0 GB",
            },
            "intelligence": None,
            "recommendations": None,
        }
        ctx.update(overrides)
        return render_template("file:pagerduty_payload.j2", ctx)

    def test_output_is_valid_json(self):
        out = self._render()
        data = json.loads(out)
        self.assertIn("summary", data)
        self.assertIn("custom_details", data)

    def test_valid_json_with_null_metrics(self):
        out = self._render(
            incident={
                "source": None,
                "generated_at": None,
                "cpu_count": None,
                "ram_total_human": None,
                "disk_total_human": None,
            }
        )
        data = json.loads(out)
        cd = data["custom_details"]
        self.assertNotIn("cpu_count", cd)
        self.assertNotIn("ram_total_human", cd)
        self.assertNotIn("disk_total_human", cd)

    def test_includes_generated_at(self):
        out = self._render()
        data = json.loads(out)
        self.assertEqual(data["custom_details"]["generated_at"], "2026-03-03T12:00:00+00:00")

    def test_valid_json_with_empty_tags_and_context(self):
        out = self._render(tags={}, context={})
        data = json.loads(out)
        self.assertIn("custom_details", data)

    def test_recommendations_capped_at_10(self):
        recs = [{"title": f"Rec {i}", "description": f"Desc {i}"} for i in range(15)]
        out = self._render(recommendations=recs)
        data = json.loads(out)
        self.assertEqual(len(data["custom_details"]["recommendations"]), 10)


class IncidentNotificationTemplateTests(SimpleTestCase):
    """Tests for incident_notification.j2 template normalization."""

    def _render(self, **overrides):
        ctx = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops",
            "tags": {},
            "context": {},
            "incident_id": 42,
            "source": "local-monitor",
            "incident": {
                "ingest": "raw ingest data",
                "check": "raw check data",
                "generated_at": "2026-03-03T12:00:00+00:00",
            },
            "intelligence": {
                "summary": "High CPU from runaway process",
                "probable_cause": "Memory leak in worker",
                "actions": ["Restart worker"],
            },
            "recommendations": [
                {"title": "Restart", "description": "Restart the worker", "priority": "high"},
            ],
        }
        ctx.update(overrides)
        return render_template("file:incident_notification.j2", ctx)

    def test_uses_incident_ingest_not_bare(self):
        """Verify template uses incident.ingest, not bare ingest variable."""
        out = self._render()
        self.assertIn("raw ingest data", out)

    def test_uses_incident_check_not_bare(self):
        """Verify template uses incident.check, not bare check variable."""
        out = self._render()
        self.assertIn("raw check data", out)

    def test_includes_generated_at(self):
        out = self._render()
        self.assertIn("2026-03-03T12:00:00", out)

    def test_recommendations_capped_at_10(self):
        recs = [
            {"title": f"Rec {i}", "description": f"Desc {i}", "priority": "low"} for i in range(15)
        ]
        out = self._render(recommendations=recs)
        self.assertIn("Rec 9", out)
        self.assertNotIn("Rec 10", out)

    def test_no_deep_nested_details_access(self):
        """Verify simplified recommendations without r.details.large_items etc."""
        recs = [{"title": "Clean disk", "description": "Remove old files", "priority": "high"}]
        out = self._render(recommendations=recs)
        self.assertIn("Clean disk", out)
        self.assertIn("Remove old files", out)
        self.assertNotIn("Large items", out)
        self.assertNotIn("Top processes", out)

    def test_renders_without_optional_fields(self):
        out = self._render(
            intelligence=None,
            recommendations=None,
            incident_id=None,
            source=None,
            incident={},
        )
        self.assertIn("CPU Alert", out)
