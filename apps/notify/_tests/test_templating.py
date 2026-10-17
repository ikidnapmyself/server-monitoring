"""Tests for templating utilities."""

from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from apps.notify.templating import (
    NotificationTemplatingService,
    _SafeDict,
    render_template,
)


def _message(context=None, tags=None):
    return {
        "title": "[CRITICAL] Disk almost full on web-1",
        "message": "body",
        "severity": "critical",
        "channel": "ops",
        "tags": tags or {},
        "context": context or {},
    }


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

    def test_context_exposes_descriptions(self):
        incident = {"headline": "h", "detail": "", "tone": "critical", "action": ""}
        ctx = NotificationTemplatingService().build_template_context(
            _message(
                {
                    "incident": incident,
                    "findings": [incident],
                    "console_url": "u",
                    "trace_id": "t",
                }
            )
        )

        assert ctx["incident"] == incident
        assert ctx["findings"] == [incident]
        assert ctx["analysis"] is None
        assert (ctx["console_url"], ctx["trace_id"]) == ("u", "t")
        assert "recommendations" not in ctx
        assert "context" not in ctx

    def test_context_falls_back_for_bare_messages(self):
        ctx = NotificationTemplatingService().build_template_context(
            _message(tags={"trace_id": "from-tags"})
        )

        assert ctx["incident"] is None
        assert ctx["findings"] == []
        assert ctx["console_url"] == ""
        assert ctx["trace_id"] == "from-tags"
        assert ctx["generated_at"]

    def test_context_reads_missing_context_and_tags(self):
        ctx = NotificationTemplatingService().build_template_context(
            {"title": "T", "message": "M", "severity": "info", "channel": "c"}
        )

        assert (ctx["tags"], ctx["trace_id"]) == ({}, "")


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

    def test_config_template_key(self):
        config = {"template": {"type": "inline", "template": "Hello {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Hello Test"

    def test_config_text_template_key(self):
        config = {"text_template": {"type": "inline", "template": "Hi {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Hi Test"

    def test_config_payload_template_key(self):
        config = {"payload_template": {"type": "inline", "template": "Payload: {{ title }}"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "Payload: Test"

    def test_config_template_render_error_raises(self):
        config = {"template": {"type": "inline", "template": "{{ foo|nonexistent_filter }}"}}
        with pytest.raises(ValueError, match="Failed to render configured template"):
            self.svc.render_message_templates("test_driver", self.base_msg, config)

    def test_default_file_fallback_chain(self):
        result = self.svc.render_message_templates("slack", self.base_msg, {})
        assert result["text"] is not None

    def test_no_template_found_raises(self):
        with pytest.raises(ValueError, match="No template found for driver"):
            self.svc.render_message_templates("nonexistent_driver", self.base_msg, {})

    def test_html_template_from_config(self):
        config = {
            "template": {"type": "inline", "template": "text content"},
            "html_template": {"type": "inline", "template": "<b>{{ title }}</b>"},
        }
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "text content"
        # Literal <b> tags in the template source are preserved as-is; variable
        # output is also unescaped. The template itself is responsible for safety.
        assert result["html"] == "<b>Test</b>"

    def test_html_template_render_error_returns_none(self):
        config = {
            "template": {"type": "inline", "template": "text content"},
            "html_template": {"type": "inline", "template": "{{ foo|nonexistent_filter }}"},
        }
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["text"] == "text content"
        assert result["html"] is None

    def test_html_default_file_fallback(self):
        result = self.svc.render_message_templates("email", self.base_msg, {})
        assert result["text"] is not None
        assert result["html"] is not None

    def test_html_default_file_missing_returns_none(self):
        config = {"template": {"type": "inline", "template": "text content"}}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        assert result["html"] is None

    def test_none_config_treated_as_empty(self):
        result = self.svc.render_message_templates("slack", self.base_msg, None)
        assert result["text"] is not None

    def test_no_template_found_includes_error_details(self):
        """When no candidates render, the error message includes details."""
        with pytest.raises(ValueError, match="No template found.*Tried"):
            self.svc.render_message_templates("totally_fake_driver", self.base_msg, {})

    def test_config_template_renders_to_none_still_accepted(self):
        """A template that renders to empty string is accepted (not None)."""
        config = {"template": ""}
        # Empty template spec is falsy, so render_template returns None
        # which means no text is set. This exercises the "template renders to None" path.
        with pytest.raises(ValueError, match="No template found"):
            self.svc.render_message_templates("test_driver", self.base_msg, config)


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

    # L334->375: config has template key, but render_template returns None.
    # Mock render_template to return None so the `if rendered is not None:` is False,
    # which then falls through to the html section at L375.
    @patch("apps.notify.templating.render_template", return_value=None)
    def test_config_template_renders_none_falls_through(self, _mock_render):
        config = {"template": "some truthy template"}
        result = self.svc.render_message_templates("test_driver", self.base_msg, config)
        # rendered was None, so text stays None; html also None
        assert result["text"] is None
        assert result["html"] is None

    # L356->351: In the default file candidates loop, rendered_def is None for one
    # candidate so the loop continues to the next. Mock render_template to return
    # None first, then a value, to exercise the continue path.
    @patch(
        "apps.notify.templating.render_template",
        side_effect=[None, "rendered text", None],
    )
    def test_default_candidates_loop_continues_on_none(self, _mock_render):
        config = {}  # no template keys, so enters else branch
        result = self.svc.render_message_templates("mydriver", self.base_msg, config)
        assert result["text"] == "rendered text"

    # L379->394: html_template in config renders successfully.
    # We need the text part to succeed first, then html_template to render.
    def test_html_template_config_with_default_text(self):
        config = {"html_template": {"type": "inline", "template": "Hello <b>{{ title }}</b>"}}
        result = self.svc.render_message_templates("slack", self.base_msg, config)
        assert result["text"] is not None  # from default slack_text.j2
        # Literal <b> tags and variable output are both unescaped; no global autoescape.
        assert result["html"] == "Hello <b>Test</b>"

    # L379->394 (False branch): html_template renders to empty string (falsy),
    # so the if check is False and we skip setting result["html"].
    def test_html_template_renders_empty_string(self):
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
    def test_default_html_file_found_email(self):
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
