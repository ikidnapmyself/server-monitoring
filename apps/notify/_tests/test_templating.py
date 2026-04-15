"""Tests for templating utilities."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from apps.notify.templating import (
    NotificationTemplatingService,
    _SafeDict,
    render_template,
)


class TemplatingTests(SimpleTestCase):
    def test_render_inline_template(self):
        out = render_template(
            {"type": "inline", "template": "Hello {{ name }}"},
            {"name": "World"},
        )
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


# ---------------------------------------------------------------------------
# render_template function tests
# ---------------------------------------------------------------------------


class TestRenderTemplateFunction(SimpleTestCase):
    def test_none_spec_returns_none(self):
        assert render_template(None, {}) is None

    def test_empty_string_spec_returns_none(self):
        assert render_template("", {}) is None

    def test_dict_spec_inline(self):
        spec = {"type": "inline", "template": "Hello {{ name }}"}
        result = render_template(spec, {"name": "World"})
        assert "Hello" in result
        assert "World" in result

    def test_dict_spec_inline_default_type(self):
        spec = {"template": "Hi {{ name }}"}
        result = render_template(spec, {"name": "there"})
        assert "Hi" in result
        assert "there" in result

    def test_dict_spec_file(self):
        spec = {"type": "file", "template": "email_text.j2"}
        result = render_template(
            spec,
            {
                "title": "T",
                "message": "M",
                "severity": "info",
                "channel": "c",
                "tags": {},
                "context": {},
                "incident": {},
            },
        )
        assert result is not None

    def test_dict_spec_file_not_found(self):
        spec = {"type": "file", "template": "nonexistent_file.j2"}
        with pytest.raises(ValueError, match="Template file not found"):
            render_template(spec, {})

    def test_dict_spec_file_traversal_rejected(self):
        """Traversal names via dict spec raise ValueError, not a file-not-found error."""
        spec = {"type": "file", "template": "../../../etc/passwd"}
        with pytest.raises(ValueError, match="Invalid template filename"):
            render_template(spec, {})

    def test_file_prefix_traversal_rejected(self):
        """Traversal names via 'file:' string spec raise ValueError."""
        with pytest.raises(ValueError, match="Invalid template filename"):
            render_template("file:../../../etc/passwd", {})

    def test_string_auto_detects_existing_file(self):
        result = render_template(
            "email_text.j2",
            {
                "title": "T",
                "message": "M",
                "severity": "info",
                "channel": "c",
                "tags": {},
                "context": {},
                "incident": {},
            },
        )
        assert result is not None

    def test_string_auto_detects_without_extension(self):
        result = render_template(
            "email_text",
            {
                "title": "T",
                "message": "M",
                "severity": "info",
                "channel": "c",
                "tags": {},
                "context": {},
                "incident": {},
            },
        )
        assert result is not None

    def test_string_with_jinja_syntax_raises(self):
        """Bare strings containing Jinja syntax are rejected to prevent SSTI
        via untrusted payload data.

        This test validates the reverse of the pre-hardening behavior: bare
        strings used to be treated as inline templates; now they must either
        be a filename (matching the restrictive pattern) or an explicit dict
        spec.
        """
        with pytest.raises(ValueError, match="bare strings must be a filename"):
            render_template("Hello {{ name }}", {"name": "X"})

    def test_unsupported_spec_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported template spec"):
            render_template(42, {})

    def test_unsupported_spec_list_raises(self):
        with pytest.raises(ValueError, match="Unsupported template spec"):
            render_template(["a", "b"], {})

    def test_jinja2_render_error_raises(self):
        with pytest.raises(ValueError, match="Jinja2 render error"):
            render_template(
                {"type": "inline", "template": "{{ foo|nonexistent_filter }}"},
                {},
            )

    def test_dict_spec_with_none_template_returns_none(self):
        spec = {"type": "inline", "template": None}
        assert render_template(spec, {}) is None


class TestRenderTemplateFallback(SimpleTestCase):
    """Test the format_map fallback when Jinja2 is unavailable."""

    def test_format_map_fallback(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            result = render_template(
                {"type": "inline", "template": "Hello {name}"},
                {"name": "World"},
            )
            assert result == "Hello World"

    def test_format_map_missing_key_returns_empty(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            result = render_template(
                {"type": "inline", "template": "Hello {name} {missing}"},
                {"name": "World"},
            )
            assert result == "Hello World "

    def test_jinja_syntax_without_jinja_raises(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            with pytest.raises(ValueError, match="Jinja2 is not installed"):
                render_template(
                    {"type": "inline", "template": "{{ name }}"},
                    {"name": "World"},
                )

    def test_jinja_block_syntax_without_jinja_raises(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            with pytest.raises(ValueError, match="Jinja2 is not installed"):
                render_template(
                    {"type": "inline", "template": "{% if true %}yes{% endif %}"},
                    {},
                )

    def test_jinja_comment_syntax_without_jinja_raises(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            with pytest.raises(ValueError, match="Jinja2 is not installed"):
                render_template(
                    {"type": "inline", "template": "{# comment #}"},
                    {},
                )

    def test_format_map_error_raises(self):
        with (
            patch("apps.notify.templating._JINJA_AVAILABLE", False),
            patch("apps.notify.templating._JINJA_ENV", None),
        ):
            with pytest.raises(ValueError, match="Fallback render error"):
                render_template({"type": "inline", "template": "{0}"}, {})


class TestSafeDict(SimpleTestCase):
    def test_missing_key_returns_empty_string(self):
        d = _SafeDict({"a": 1})
        assert d["missing"] == ""

    def test_existing_key_returns_value(self):
        d = _SafeDict({"a": 1})
        assert d["a"] == 1


# ---------------------------------------------------------------------------
# NotificationTemplatingService tests
# ---------------------------------------------------------------------------


class TestComposeIncidentDetails(SimpleTestCase):
    def setUp(self):
        self.svc = NotificationTemplatingService()
        self.base_msg = {
            "title": "CPU Alert",
            "message": "CPU usage at 95%",
            "severity": "critical",
            "channel": "ops",
            "tags": {"source": "prometheus"},
            "context": {},
        }

    @patch("apps.notify.templating.psutil")
    def test_psutil_fallbacks_used_when_context_empty(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 8
        mock_psutil.virtual_memory.return_value = MagicMock(total=17179869184)
        mock_psutil.disk_usage.return_value = MagicMock(total=536870912000)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["cpu_count"] == 8
        assert result["ram_total_bytes"] == 17179869184
        assert result["ram_total_human"] == "16.0 GB"
        assert result["disk_total_bytes"] == 536870912000
        assert result["disk_total_human"] == "500.0 GB"

    @patch("apps.notify.templating.psutil")
    def test_context_values_preferred_over_psutil(self, mock_psutil):
        msg = {
            **self.base_msg,
            "context": {
                "cpu_count": 4,
                "total_memory": 8589934592,
                "disk_total": 107374182400,
            },
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["cpu_count"] == 4
        assert result["ram_total_bytes"] == 8589934592
        assert result["disk_total_bytes"] == 107374182400
        mock_psutil.cpu_count.assert_not_called()

    @patch("apps.notify.templating.psutil")
    def test_psutil_cpu_exception_returns_none(self, mock_psutil):
        mock_psutil.cpu_count.side_effect = OSError("no cpu info")
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["cpu_count"] is None

    @patch("apps.notify.templating.psutil")
    def test_psutil_memory_exception_returns_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.side_effect = OSError("no mem info")
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["ram_total_bytes"] is None
        assert result["ram_total_human"] is None

    @patch("apps.notify.templating.psutil")
    def test_psutil_disk_exception_returns_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.side_effect = OSError("no disk info")

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["disk_total_bytes"] is None
        assert result["disk_total_human"] is None

    @patch("apps.notify.templating.psutil")
    def test_recommendations_from_context(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {**self.base_msg, "context": {"recommendations": [{"title": "Fix it"}]}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [{"title": "Fix it"}]

    @patch("apps.notify.templating.psutil")
    def test_recommendations_from_intelligence(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"intelligence": {"recommendations": [{"title": "Scale up"}]}},
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [{"title": "Scale up"}]

    @patch("apps.notify.templating.psutil")
    def test_recommendations_dict_of_dicts_normalized_to_list(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {
                "recommendations": {
                    "r1": {"title": "Fix A"},
                    "r2": {"title": "Fix B"},
                }
            },
        }
        result = self.svc.compose_incident_details(msg, {})
        assert isinstance(result["recommendations"], list)
        assert len(result["recommendations"]) == 2

    @patch("apps.notify.templating.psutil")
    def test_recommendations_single_dict_wrapped_in_list(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"recommendations": {"key": "value"}},
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [{"key": "value"}]

    @patch("apps.notify.templating.psutil")
    def test_recommendations_scalar_wrapped_in_list(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"intelligence": {"recommendations": 42}},
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [42]

    @patch("apps.notify.templating.psutil")
    def test_recommendations_fallback_to_details(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"details": {"info": "some details"}},
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [{"info": "some details"}]

    @patch("apps.notify.templating.psutil")
    def test_gb_helper_with_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.side_effect = OSError
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["ram_total_human"] is None

    @patch("apps.notify.templating.psutil")
    def test_incident_id_from_context(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {**self.base_msg, "context": {"incident_id": 42}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["incident_id"] == 42

    @patch("apps.notify.templating.psutil")
    def test_source_from_tags_fallback(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert result["source"] == "prometheus"

    @patch("apps.notify.templating.psutil")
    def test_generated_at_present(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.compose_incident_details(self.base_msg, {})
        assert "generated_at" in result
        assert result["generated_at"] is not None

    @patch("apps.notify.templating.psutil")
    def test_context_alt_key_cpu_physical_count(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 99
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {**self.base_msg, "context": {"cpu_physical_count": 2}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["cpu_count"] == 2

    @patch("apps.notify.templating.psutil")
    def test_context_alt_key_memory_total(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=99)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {**self.base_msg, "context": {"memory_total": 5000}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["ram_total_bytes"] == 5000

    @patch("apps.notify.templating.psutil")
    def test_context_alt_key_ram_total(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=99)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {**self.base_msg, "context": {"ram_total": 7000}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["ram_total_bytes"] == 7000

    @patch("apps.notify.templating.psutil")
    def test_context_alt_key_total_disk(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=99)

        msg = {**self.base_msg, "context": {"total_disk": 9000}}
        result = self.svc.compose_incident_details(msg, {})
        assert result["disk_total_bytes"] == 9000

    @patch("apps.notify.templating.psutil")
    def test_intelligence_non_list_non_dict_recs(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"intelligence": {"recommendations": 42}},
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [42]


class TestRenderMessageTemplates(SimpleTestCase):
    def setUp(self):
        self.svc = NotificationTemplatingService()
        self.base_msg = {
            "title": "Test",
            "message": "Test message",
            "severity": "info",
            "channel": "ops",
            "tags": {},
            "context": {},
        }

    @patch("apps.notify.templating.psutil")
    def test_config_template_key(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"template": {"type": "inline", "template": "Hello {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Hello Test"

    @patch("apps.notify.templating.psutil")
    def test_config_text_template_key(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"text_template": {"type": "inline", "template": "Hi {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Hi Test"

    @patch("apps.notify.templating.psutil")
    def test_config_payload_template_key(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"payload_template": {"type": "inline", "template": "Payload: {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Payload: Test"

    @patch("apps.notify.templating.psutil")
    def test_config_template_render_error_raises(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"template": {"type": "inline", "template": "{{ foo|nonexistent_filter }}"}}
        with pytest.raises(ValueError, match="Failed to render configured template"):
            self.svc.render_message_templates("test_driver", self.base_msg, config)

    @patch("apps.notify.templating.psutil")
    def test_default_file_fallback_chain(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.render_message_templates("slack", self.base_msg, {})
        assert result["text"] is not None

    @patch("apps.notify.templating.psutil")
    def test_no_template_found_raises(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        with pytest.raises(ValueError, match="No template found for driver"):
            self.svc.render_message_templates("nonexistent_driver", self.base_msg, {})

    @patch("apps.notify.templating.psutil")
    def test_html_template_from_config(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {
            "template": {"type": "inline", "template": "text content"},
            "html_template": {"type": "inline", "template": "<b>{{ title }}</b>"},
        }
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "text content"
        # Literal <b> tags in the template source are preserved as-is; variable
        # output is also unescaped. The template itself is responsible for safety.
        assert result["html"] == "<b>Test</b>"

    @patch("apps.notify.templating.psutil")
    def test_html_template_render_error_returns_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {
            "template": {"type": "inline", "template": "text content"},
            "html_template": {"type": "inline", "template": "{{ foo|nonexistent_filter }}"},
        }
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "text content"
        assert result["html"] is None

    @patch("apps.notify.templating.psutil")
    def test_html_default_file_fallback(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.render_message_templates("email", self.base_msg, {})
        assert result["text"] is not None
        assert result["html"] is not None

    @patch("apps.notify.templating.psutil")
    def test_html_default_file_missing_returns_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"template": {"type": "inline", "template": "text content"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["html"] is None

    @patch("apps.notify.templating.psutil")
    def test_none_config_treated_as_empty(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        result = self.svc.render_message_templates("slack", self.base_msg, None)
        assert result["text"] is not None

    @patch("apps.notify.templating.psutil")
    def test_no_template_found_includes_error_details(self, mock_psutil):
        """When no candidates render, the error message includes details."""
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        with pytest.raises(ValueError, match="No template found.*Tried"):
            self.svc.render_message_templates("totally_fake_driver", self.base_msg, {})

    @patch("apps.notify.templating.psutil")
    def test_config_template_renders_to_none_still_accepted(self, mock_psutil):
        """A template that renders to empty string is accepted (not None)."""
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"template": ""}
        # Empty template spec is falsy, so render_template returns None
        # which means no text is set. This exercises the "template renders to None" path.
        with pytest.raises(ValueError, match="No template found"):
            self.svc.render_message_templates("test_driver", self.base_msg, config)


class TestComposeIncidentDetailsPprintFallback(SimpleTestCase):
    """Test the pprint exception fallback in compose_incident_details."""

    @patch("apps.notify.templating.psutil")
    @patch("apps.notify.templating.pprint.pformat", side_effect=Exception("pprint broke"))
    def test_pprint_exception_falls_back_to_str(self, _mock_pformat, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        svc = NotificationTemplatingService()
        msg = {
            "title": "T",
            "message": "M",
            "severity": "info",
            "channel": "c",
            "tags": {},
            "context": {"recommendations": [{"title": "Fix"}]},
        }
        result = svc.compose_incident_details(msg, {})
        assert result["recommendations_pretty"] is not None

    @patch("apps.notify.templating.psutil")
    def test_recommendations_none_when_no_context(self, mock_psutil):
        """When context has no recs, details, or intelligence, falls back to context itself."""
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        svc = NotificationTemplatingService()
        msg = {
            "title": "T",
            "message": "M",
            "severity": "info",
            "channel": "c",
            "tags": {},
            "context": {},
        }
        result = svc.compose_incident_details(msg, {})
        # Falls back to ctx itself which is {}, and empty dict is falsy
        # so recommendations = ctx = {} which normalizes to [{}]
        assert isinstance(result["recommendations"], list)


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestCoverageGaps(SimpleTestCase):
    """Tests targeting specific branch coverage gaps in templating.py."""

    def setUp(self):
        self.svc = NotificationTemplatingService()
        self.base_msg = {
            "title": "Test",
            "message": "Test message",
            "severity": "info",
            "channel": "ops",
            "tags": {},
            "context": {},
        }

    # L214->218: intelligence is a dict but intelligence["recommendations"] is None,
    # so _int_recs is None, the elif is False, and we fall through to line 218.
    # Also no "details" key, so fallback is ctx itself.
    @patch("apps.notify.templating.psutil")
    def test_recommendations_fallback_intelligence_recs_none(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"intelligence": {"recommendations": None}},
        }
        result = self.svc.compose_incident_details(msg, {})
        # recommendations is None from intelligence, falls to line 218,
        # ctx.get("details") is None, so falls back to ctx itself
        assert isinstance(result["recommendations"], list)

    # L214->218 variant: intelligence dict with recs=None, but "details" key present
    @patch("apps.notify.templating.psutil")
    def test_recommendations_fallback_intelligence_none_with_details(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {
                "intelligence": {"recommendations": None},
                "details": {"info": "fallback details"},
            },
        }
        result = self.svc.compose_incident_details(msg, {})
        assert result["recommendations"] == [{"info": "fallback details"}]

    # L243->248: isinstance(recs_pretty, str) is False.
    # Mock pformat to return a non-string so the isinstance check is False.
    @patch("apps.notify.templating.psutil")
    @patch("apps.notify.templating.pprint.pformat", return_value=42)
    def test_pprint_returns_non_string_skips_replace(self, _mock_pformat, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        msg = {
            **self.base_msg,
            "context": {"recommendations": [{"title": "Fix"}]},
        }
        result = self.svc.compose_incident_details(msg, {})
        # pformat returned 42 (non-str), so recs_pretty stays 42, no .replace() call
        assert result["recommendations_pretty"] == 42

    # L334->375: config has template key, but render_template returns None.
    # Mock render_template to return None so the `if rendered is not None:` is False,
    # which then falls through to the html section at L375.
    @patch("apps.notify.templating.psutil")
    @patch("apps.notify.templating.render_template", return_value=None)
    def test_config_template_renders_none_falls_through(self, _mock_render, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"template": "some truthy template"}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        # rendered was None, so text stays None; html also None
        assert result["text"] is None
        assert result["html"] is None

    # L356->351: In the default file candidates loop, rendered_def is None for one
    # candidate so the loop continues to the next. Mock render_template to return
    # None first, then a value, to exercise the continue path.
    @patch("apps.notify.templating.psutil")
    @patch(
        "apps.notify.templating.render_template",
        side_effect=[None, "rendered text", None],
    )
    def test_default_candidates_loop_continues_on_none(self, _mock_render, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {}  # no template keys, so enters else branch
        result = self.svc.render_message_templates("mydriver", self.base_msg, config)
        assert result["text"] == "rendered text"

    # L379->394: html_template in config renders successfully.
    # We need the text part to succeed first, then html_template to render.
    @patch("apps.notify.templating.psutil")
    def test_html_template_config_with_default_text(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"html_template": {"type": "inline", "template": "Hello <b>{{ title }}</b>"}}
        result = self.svc.render_message_templates("slack", self.base_msg, config)
        assert result["text"] is not None  # from default slack_text.j2
        # Literal <b> tags and variable output are both unescaped; no global autoescape.
        assert result["html"] == "Hello <b>Test</b>"

    # L379->394 (False branch): html_template renders to empty string (falsy),
    # so the if check is False and we skip setting result["html"].
    @patch("apps.notify.templating.psutil")
    def test_html_template_renders_empty_string(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        # Template that renders to empty string
        config = {
            "template": {"type": "inline", "template": "text"},
            "html_template": {"type": "inline", "template": "{{ missing_var }}"},
        }
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "text"
        # Jinja2 renders undefined variables to empty string, which is falsy
        assert result["html"] is None

    # L388->394: No html_template in config, default HTML file found.
    # Using "email" which has email_html.j2.
    @patch("apps.notify.templating.psutil")
    def test_default_html_file_found_email(self, mock_psutil):
        mock_psutil.cpu_count.return_value = 1
        mock_psutil.virtual_memory.return_value = MagicMock(total=1)
        mock_psutil.disk_usage.return_value = MagicMock(total=1)

        config = {"some_other_key": "value"}
        result = self.svc.render_message_templates("email", self.base_msg, config)
        assert result["text"] is not None
        assert result["html"] is not None


# ---------------------------------------------------------------------------
# SSTI regression tests
#
# These tests guard the two hardening invariants of render_template:
#   1. Bare strings are never treated as inline Jinja2 source. The only way
#      to execute Jinja2 source through render_template is via an explicit
#      {"type": "inline", "template": "..."} dict — which callers only use
#      for trusted, DB-sourced configuration.
#   2. Even trusted inline templates run through jinja2.sandbox's
#      ImmutableSandboxedEnvironment, which blocks the attribute-access
#      primitives (``__class__``, ``__globals__``, etc.) that every known
#      Jinja SSTI payload relies on.
# ---------------------------------------------------------------------------


class TestSSTIRegression(SimpleTestCase):
    """Regression tests for the SSTI hardening in apps/notify/templating.py."""

    def test_bare_string_with_jinja_expression_rejected(self):
        """A bare string containing ``{{ ... }}`` must be rejected outright."""
        with pytest.raises(ValueError, match="bare strings must be a filename"):
            render_template("{{ 7*7 }}", {})

    def test_bare_string_with_jinja_block_rejected(self):
        """A bare string containing ``{% ... %}`` must be rejected outright."""
        with pytest.raises(ValueError, match="bare strings must be a filename"):
            render_template("{% import os %}", {})

    def test_sandbox_blocks_dunder_class_mro_access(self):
        """Classic SSTI gadget ``''.__class__.__mro__`` must be blocked.

        ImmutableSandboxedEnvironment rejects dunder attribute access and
        raises ``jinja2.sandbox.SecurityError``. ``render_template`` wraps all
        Jinja exceptions in ``ValueError(f"Jinja2 render error: {e}")``, so
        callers see a ValueError whose message contains the sandbox's
        "unsafe" text.

        Note: bare ``''.__class__`` does NOT raise — Jinja2 silently returns
        an Undefined value. The sandbox only fires when something *uses* the
        forbidden attribute, e.g. walks into ``__mro__``. All known SSTI
        gadgets dereference at least one dunder chain, so this is what we
        must block.
        """
        with pytest.raises(ValueError, match="Jinja2 render error") as excinfo:
            render_template(
                {"type": "inline", "template": "{{ ''.__class__.__mro__ }}"},
                {},
            )
        assert "unsafe" in str(excinfo.value).lower()

    def test_sandbox_blocks_subclasses_gadget(self):
        """Full ``__class__.__mro__[...]__subclasses__`` gadget must be blocked."""
        with pytest.raises(ValueError, match="Jinja2 render error") as excinfo:
            render_template(
                {
                    "type": "inline",
                    "template": "{{ ''.__class__.__mro__[1].__subclasses__() }}",
                },
                {},
            )
        assert "unsafe" in str(excinfo.value).lower()

    def test_inline_dict_form_renders_safely(self):
        """The dict form is the trusted path and must still work."""
        out = render_template(
            {"type": "inline", "template": "Hello {{ name }}"},
            {"name": "world"},
        )
        assert out == "Hello world"

    def test_inline_template_does_not_autoescape_variables(self):
        """Inline templates do NOT autoescape variable output.

        Global autoescape is disabled so Slack/JSON/text templates are not
        silently broken by HTML entity encoding (e.g. Slack's ``<url|text>``
        link syntax would become ``&lt;url|text&gt;``). HTML safety is the
        responsibility of individual templates via explicit ``|e`` or ``|tojson``.
        """
        out = render_template(
            {"type": "inline", "template": "{{ x }}"},
            {"x": "<b>bold</b>"},
        )
        # No global autoescape: raw angle brackets are preserved in output
        assert "<b>bold</b>" in out

    def test_explicit_e_filter_escapes_html(self):
        """The ``|e`` filter provides explicit HTML escaping for HTML templates."""
        out = render_template(
            {"type": "inline", "template": "{{ x | e }}"},
            {"x": "<script>alert(1)</script>"},
        )
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
