"""Simple templating utility for notify channels.

Supports Jinja2 when available (recommended). Falls back to Python's
str.format_map for basic templating if Jinja2 is not installed.

Template spec accepted by render_template:
- None or empty -> returns None
- string starting with "file:<name>" -> loads file from apps/notify/templates/<name>
- dict: {"type": "inline"|"file", "template": "..."}
- string (default) -> treated as inline template

The render_template function returns a rendered string or raises a
ValueError on invalid template.
"""

from __future__ import annotations

import logging
import pprint
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

import psutil

TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import jinja2

# Declare the env with an Optional type so assigning None in the except branch
# doesn't conflict with the Environment type during type-checking.
_JINJA_ENV: Optional["jinja2.Environment"] = None

try:
    import jinja2

    _JINJA_AVAILABLE = True
    _JINJA_ENV = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )
except Exception:
    _JINJA_AVAILABLE = False
    _JINJA_ENV = None


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def _load_template_from_file(name: str) -> Optional[str]:
    path = TEMPLATES_DIR / name
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    # try with .j2 extension
    path2 = TEMPLATES_DIR / (name + ".j2")
    if path2.exists() and path2.is_file():
        return path2.read_text(encoding="utf-8")
    return None


def render_template(spec: Any, context: Dict[str, Any]) -> Optional[str]:
    """Render a template spec with the provided context.

    Args:
        spec: template spec (None, string, or dict)
        context: mapping of variables for the template

    Returns:
        Rendered string or None if spec is falsy
    """
    if not spec:
        return None

    template_str: Optional[str] = None
    template_name: Optional[str] = None
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
        else:
            # If the provided string looks like a template filename (e.g. "slack_text.j2"
            # or "slack_text") and a file exists in TEMPLATES_DIR, treat it as a file
            # reference so DB-stored template names work without requiring the "file:" prefix.
            maybe = spec
            # try exact match and with .j2
            if _load_template_from_file(maybe) is not None:
                template_name = maybe
            else:
                template_str = spec
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
    """Service for handling notification templating logic.

    Follows Single Responsibility Principle: handles all template-related operations
    including context building, incident details composition, and template rendering.
    """

    def compose_incident_details(
        self, message_dict: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compose a common incident detail payload used by all drivers.

        Args:
            message_dict: Dictionary representation of the notification message.
            config: Driver-specific configuration.

        Returns:
            Dict containing normalized metrics, context, summaries and recommendations.
        """
        ctx = message_dict.get("context", {}) or {}

        # Metrics: prefer explicit context values, otherwise fallback to psutil
        cpu_count = ctx.get("cpu_count") or ctx.get("cpu_physical_count") or None
        try:
            if cpu_count is None:
                cpu_count = psutil.cpu_count()
        except Exception:
            cpu_count = None

        try:
            ram_total_bytes = (
                ctx.get("total_memory")
                or ctx.get("memory_total")
                or ctx.get("ram_total")
                or psutil.virtual_memory().total
            )
        except Exception:
            ram_total_bytes = None

        try:
            disk_total_bytes = (
                ctx.get("disk_total") or ctx.get("total_disk") or psutil.disk_usage("/").total
            )
        except Exception:
            disk_total_bytes = None

        def _gb(b):
            try:
                return f"{float(b) / (1024 ** 3):.1f} GB"
            except Exception:
                return None

        # Determine recommendations in a type-safe way
        # Fetch recommendations from context or intelligence safely so mypy can
        # narrow types before calling .get on dicts.
        # Start with a permissive type; we'll normalize below.
        recommendations: Any = None

        _recs = ctx.get("recommendations")
        if isinstance(_recs, (list, dict)):
            recommendations = _recs
        else:
            intelligence = ctx.get("intelligence")
            if isinstance(intelligence, dict):
                _int_recs = intelligence.get("recommendations")
                if isinstance(_int_recs, (list, dict)):
                    recommendations = _int_recs
                elif _int_recs is not None:
                    # keep scalar or other value; normalize later
                    recommendations = _int_recs

            if recommendations is None:
                # fallback to context 'details' or whole context
                recommendations = ctx.get("details") or ctx

        # Normalize recommendations to a list when possible. Templates (e.g. slack_text.j2)
        # expect to index/slice and iterate over recommendations. If we pass a dict,
        # Jinja will interpret `recs[:5]` as a dict lookup with a slice object and
        # raise "unhashable type: 'slice'". To avoid that, convert common dict forms
        # into a list of recommendation dicts. If recommendations is already a list
        # or is falsy, leave it as-is.
        if isinstance(recommendations, dict):
            # If the dict looks like a mapping of ids -> rec dicts, use the values.
            vals = list(recommendations.values())
            if vals and all(isinstance(v, dict) for v in vals):
                recommendations = vals
            else:
                # Treat the whole dict as a single recommendation entry.
                recommendations = [recommendations]
        elif recommendations is not None and not isinstance(recommendations, list):
            # Wrap scalars or other single objects into a list so templates can iterate.
            recommendations = [recommendations]

        # Pretty printed recommendations for human-facing outputs
        try:
            recs_pretty = pprint.pformat(recommendations, width=120, depth=4)
            if isinstance(recs_pretty, str):
                recs_pretty = recs_pretty.replace("\\n", "\n")
        except Exception:
            recs_pretty = str(recommendations)

        incident_details = {
            "title": message_dict["title"],
            "message": message_dict["message"],
            "severity": message_dict["severity"],
            "channel": message_dict["channel"],
            "tags": message_dict["tags"],
            "context": ctx,
            "cpu_count": cpu_count,
            "ram_total_bytes": ram_total_bytes,
            "ram_total_human": _gb(ram_total_bytes),
            "disk_total_bytes": disk_total_bytes,
            "disk_total_human": _gb(disk_total_bytes),
            "incident_id": ctx.get("incident_id") or ctx.get("id"),
            "source": ctx.get("source") or message_dict["tags"].get("source") or None,
            "environment": ctx.get("environment"),
            "ingest": ctx.get("ingest"),
            "check": ctx.get("check"),
            "intelligence": ctx.get("intelligence"),
            "recommendations": recommendations,
            "recommendations_pretty": recs_pretty,
            # Use timezone-aware UTC timestamp
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return incident_details

    def build_template_context(
        self, message_dict: Dict[str, Any], incident_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build the template rendering context for message templates.

        Args:
            message_dict: Dictionary representation of the notification message.
            incident_details: Composed incident details.

        Returns:
            Dict containing all variables available in templates.
        """
        return {
            "title": message_dict["title"],
            "message": message_dict["message"],
            "severity": message_dict["severity"],
            "channel": message_dict["channel"],
            "tags": message_dict["tags"],
            "context": message_dict["context"],
            "incident": incident_details,
            # Convenience top-level aliases for templates expecting these names
            "intelligence": incident_details.get("intelligence"),
            "recommendations": incident_details.get("recommendations"),
            "incident_id": incident_details.get("incident_id"),
            "source": incident_details.get("source"),
        }

    def render_message_templates(
        self, driver_name: str, message_dict: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Optional[str]]:
        """Render per-driver templates from config.

        Args:
            driver_name: Name of the driver (e.g., 'slack').
            message_dict: Dictionary representation of the notification message.
            config: Driver-specific configuration.

        Returns:
            Dict with optional 'text' and 'html' keys (values or None).
        """
        result: Dict[str, Optional[str]] = {"text": None, "html": None}
        config = config or {}

        logger.debug(
            "render_message_templates: driver=%s, config_keys=%s", driver_name, list(config.keys())
        )
        # Track which template source was used for diagnostics
        used_template_source: Optional[str] = None

        incident_details = self.compose_incident_details(message_dict, config)
        ctx = self.build_template_context(message_dict, incident_details)

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
            errors: Dict[str, str] = {}
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

        # Try html_template
        html_tmpl = config.get("html_template")
        if html_tmpl:
            try:
                rendered_html = render_template(html_tmpl, ctx)
                if rendered_html:
                    result["html"] = rendered_html
            except Exception:
                result["html"] = None

        logger.debug(
            "render_message_templates: driver=%s used=%s text_len=%s html_len=%s",
            driver_name,
            used_template_source,
            len(result.get("text") or "") if result.get("text") is not None else None,
            len(result.get("html") or "") if result.get("html") is not None else None,
        )

        return result
