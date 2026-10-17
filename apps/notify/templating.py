"""Simple templating utility for notify channels.

Uses Jinja2 with a sandboxed environment to prevent SSTI. Falls back to
Python's str.format_map for basic templating if Jinja2 is not installed.

Template spec accepted by render_template:
- None or empty -> returns None
- string starting with "file:<name>" -> loads file from apps/notify/templates/<name>
- bare string matching a filename pattern -> treated as "file:<name>" (backcompat)
- dict: {"type": "inline"|"file", "template": "..."}
- any other bare string -> ValueError (inline templates must use dict form;
  this prevents untrusted request payloads from injecting raw Jinja2 source)

The render_template function returns a rendered string or raises a
ValueError on invalid template.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.security import PathNotAllowedError, resolve_safe_name

TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)

# A bare string is accepted as a file reference only if it matches this
# restrictive filename pattern. Anything else (e.g. a string containing Jinja
# syntax, spaces, slashes) is rejected so untrusted payloads cannot inject
# inline Jinja2 source via the bare-string form.
_FILENAME_PATTERN = re.compile(r"^[\w-]+(\.[\w]+)?$")

try:
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    # Optional type so assigning None in the except branch doesn't conflict
    # with the Environment type during type-checking.
    _JINJA_ENV: jinja2.Environment | None = None
    _JINJA_AVAILABLE = True
    # ImmutableSandboxedEnvironment blocks access to dunder attributes
    # (__class__, __globals__, __init__, __mro__, __subclasses__) which are
    # the building blocks of all known Jinja SSTI gadgets.
    # autoescape is intentionally disabled globally: templates are responsible
    # for their own escaping. HTML templates (e.g. email_html.j2) use the |e
    # filter on every variable; JSON templates use |tojson. Enabling global
    # autoescape would silently break Slack/text/JSON templates that contain
    # angle-bracket content (e.g. Slack's <url|text> link syntax).
    _JINJA_ENV = ImmutableSandboxedEnvironment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )
except Exception:  # pragma: no cover
    _JINJA_AVAILABLE = False  # pragma: no cover
    _JINJA_ENV = None  # pragma: no cover


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _load_template_from_file(name: str) -> str | None:
    try:
        name = resolve_safe_name(name)
    except PathNotAllowedError:
        raise ValueError("Invalid template filename: contains invalid path components") from None
    path = TEMPLATES_DIR / name
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    # try with .j2 extension
    path2 = TEMPLATES_DIR / (name + ".j2")
    if path2.exists() and path2.is_file():
        return path2.read_text(encoding="utf-8")
    return None


def render_template(spec: Any, context: dict[str, Any]) -> str | None:
    """Render a template spec with the provided context.

    Args:
        spec: template spec (None, string, or dict)
        context: mapping of variables for the template

    Returns:
        Rendered string or None if spec is falsy
    """
    if not spec:
        return None

    template_str: str | None = None
    template_name: str | None = None
    # normalize spec
    if isinstance(spec, dict):
        ttype = spec.get("type", "inline")
        if ttype == "file":
            template_name = spec.get("template")
        else:
            template_str = spec.get("template")
    elif isinstance(spec, str):
        if spec.startswith("file:"):
            template_name = spec.split(":", 1)[1]
        elif _FILENAME_PATTERN.match(spec):
            # Bare string matching a filename pattern is treated as a file
            # reference (backcompat for DB configs). Anything containing Jinja
            # syntax, spaces, newlines, or path separators is rejected below.
            template_name = spec
        else:
            raise ValueError(
                "Invalid template spec: bare strings must be a filename. "
                "Use dict form {'type': 'inline', 'template': '...'} for inline "
                "templates, or 'file:<name>' to reference a file."
            )
    else:
        # unsupported spec
        raise ValueError("Unsupported template spec")

    if template_name:
        logger.debug("render_template: loading template file: %s", template_name)
        template_str = _load_template_from_file(template_name)
        if template_str is None:
            logger.debug("render_template: template file not found: %s", template_name)
            raise ValueError(f"Template file not found: {template_name}")
        logger.debug("render_template: loaded %s (len=%d)", template_name, len(template_str))

    if template_str is None:
        return None

    # Render using Jinja2 when available
    if _JINJA_AVAILABLE and _JINJA_ENV is not None:
        try:
            tmpl = _JINJA_ENV.from_string(template_str)
            rendered = tmpl.render(**(context or {}))
            logger.debug("render_template: jinja2 rendered len=%d", len(rendered))
            return rendered
        except Exception as e:
            raise ValueError(f"Jinja2 render error: {e}")

    # If template contains Jinja-specific syntax but Jinja2 isn't available,
    # raise a clear error so the user can install Jinja2 instead of failing
    # with an obscure fallback error.
    if not _JINJA_AVAILABLE and (
        "{{" in template_str or "{%" in template_str or "{#" in template_str
    ):
        raise ValueError(
            "Template appears to use Jinja2 syntax but Jinja2 is not installed. "
            "Install it (e.g. `pip install jinja2`) or provide a plain Python-style template."
        )

    # Fallback to Python format_map for very simple templates
    try:
        # Use SafeDict so missing keys become empty strings
        rendered = template_str.format_map(_SafeDict(context or {}))
        logger.debug("render_template: fallback formatted len=%d", len(rendered))
        return rendered
    except Exception as e:
        raise ValueError(f"Fallback render error: {e}")


class NotificationTemplatingService:
    """Builds the template context and renders each driver's templates from it."""

    def build_template_context(self, message_dict: dict[str, Any]) -> dict[str, Any]:
        """Every variable a notification template can read."""
        ctx = message_dict.get("context") or {}
        tags = message_dict.get("tags") or {}
        return {
            "title": message_dict["title"],
            "message": message_dict["message"],
            "severity": message_dict["severity"],
            "channel": message_dict["channel"],
            "tags": tags,
            "trace_id": ctx.get("trace_id") or tags.get("trace_id") or "",
            "incident": ctx.get("incident"),
            "findings": ctx.get("findings") or [],
            "analysis": ctx.get("analysis"),
            "console_url": ctx.get("console_url") or "",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def render_message_templates(
        self, driver_name: str, message_dict: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, str | None]:
        """Render per-driver templates from config.

        Args:
            driver_name: Name of the driver (e.g., 'slack').
            message_dict: Dictionary representation of the notification message.
            config: Driver-specific configuration.

        Returns:
            Dict with optional 'text' and 'html' keys (values or None).
        """
        result: dict[str, str | None] = {"text": None, "html": None}
        config = config or {}

        logger.debug(
            "render_message_templates: driver=%s, config_keys=%s", driver_name, list(config.keys())
        )
        # Track which template source was used for diagnostics
        used_template_source: str | None = None

        ctx = self.build_template_context(message_dict)

        # Try various config keys that may specify a template (template/text/html/payload)
        tmpl = (
            config.get("template") or config.get("text_template") or config.get("payload_template")
        )
        if tmpl:
            try:
                rendered = render_template(tmpl, ctx)
                # Accept rendered strings including empty string; only treat None as missing
                if rendered is not None:
                    result["text"] = rendered
                    used_template_source = f"config:{tmpl}"
            except Exception as e:
                # Provide a clearer error including the underlying render exception
                raise ValueError(
                    f"Failed to render configured template for driver '{driver_name}': {e}"
                ) from e
        else:
            # If no explicit template configured, try driver-specific default files in order
            tried = []
            errors: dict[str, str] = {}
            candidates = [
                f"file:{driver_name}_text.j2",
                f"file:{driver_name}_payload.j2",
                f"file:{driver_name}.j2",
            ]
            for candidate in candidates:
                tried.append(candidate)
                try:
                    rendered_def = render_template(candidate, ctx)
                    # Accept rendered strings including empty string; only treat None as missing
                    if rendered_def is not None:
                        result["text"] = rendered_def
                        used_template_source = candidate
                        break
                except Exception as e:
                    # record render error and continue
                    errors[candidate] = str(e)
                    continue
            # If still nothing, we must fail: driver requires a template
            # Treat empty string as a valid rendered template; only None means missing
            if result.get("text") is None:
                # Include candidate errors for easier debugging
                error_lines = [f"{k}: {v}" for k, v in errors.items()]
                error_detail = "; ".join(error_lines) if error_lines else "no candidates rendered"
                raise ValueError(
                    f"No template found for driver '{driver_name}'. Tried config keys and files: {tried}. Details: {error_detail}"
                )

        # Try html_template from config first, then driver-default HTML file
        html_tmpl = config.get("html_template")
        if html_tmpl:
            try:
                rendered_html = render_template(html_tmpl, ctx)
                if rendered_html:
                    result["html"] = rendered_html
            except Exception:
                result["html"] = None
        else:
            # Try driver-default HTML template file
            html_candidate = f"file:{driver_name}_html.j2"
            try:
                rendered_html = render_template(html_candidate, ctx)
                if rendered_html:
                    result["html"] = rendered_html
            except Exception:
                # HTML template is optional, don't fail if not found
                pass

        logger.debug(
            "render_message_templates: driver=%s used=%s text_len=%s html_len=%s",
            driver_name,
            used_template_source,
            len(result.get("text") or "") if result.get("text") is not None else None,
            len(result.get("html") or "") if result.get("html") is not None else None,
        )

        return result
