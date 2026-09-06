"""Read back the re-evaluation record an alert carries, as sentences.

Two writers stamp a record onto ``Alert.annotations``, both as a JSON *string*:
``apps.alerts.reevaluation.reevaluate_severity`` writes ``severity_reevaluated`` when
a push is re-scored at ingest, and ``apps.alerts.reeval_existing.apply_reeval`` writes
``reevaluated_on_config_change`` when an operator applies a policy change. This module
only reads them.

Every failure renders a sentence. The alert change page must open even when a node
sent something unreadable, so nothing here raises.
"""

import json

from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.html import format_html_join
from django.utils.timezone import is_aware, localtime

from apps.alerts.reevaluation import format_value

NEVER = "Never re-evaluated."
UNREADABLE = "Could not read the re-evaluation record."

RECORD_LABELS = [
    ("severity_reevaluated", "At ingest"),
    ("reevaluated_on_config_change", "On a policy change"),
]


def _load(raw) -> dict | None:
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _severity_clause(record: dict) -> str | None:
    old, new = record.get("from"), record.get("to")
    if not old or not new:
        return None
    return f"{str(old).upper()} to {str(new).upper()}"


def _status_clause(record: dict) -> str | None:
    old, new = record.get("status_from"), record.get("status_to")
    if not old or not new or old == new:
        return None
    return f"status {old} to {new}"


def _when_clause(record: dict) -> str | None:
    at = record.get("at")
    if not isinstance(at, str):
        return None
    try:
        moment = parse_datetime(at)
    except ValueError:
        return None
    if moment is None:
        return None
    if is_aware(moment):
        moment = localtime(moment)
    return date_format(moment, "j M Y")


def _policy_clause(record: dict) -> str | None:
    """How the policy that produced this score reads.

    ``thresholds`` is the raw ``Node.config`` entry for the checker, so its shape
    depends on the checker: a numeric one carries warning/critical, listening_ports
    carries an allowlist of ports instead.
    """
    thresholds = record.get("thresholds")
    if not isinstance(thresholds, dict):
        return None
    parts = []
    if thresholds.get("warning_threshold") is not None:
        parts.append(f"warning {format_value(thresholds['warning_threshold'])}")
    if thresholds.get("critical_threshold") is not None:
        parts.append(f"critical {format_value(thresholds['critical_threshold'])}")
    if parts:
        return "policy " + " / ".join(parts)
    allowlist = thresholds.get("allowlist")
    if not isinstance(allowlist, list):
        return None
    if not allowlist:
        return "policy allows no ports"
    return "policy allows ports " + ", ".join(format_value(port) for port in allowlist)


def describe_record(record: dict) -> str:
    """One sentence for one record, saying only what the record actually carries."""
    parts = []
    severity = _severity_clause(record)
    when = _when_clause(record)
    if severity and when:
        parts.append(f"{severity} on {when}")
    elif severity:
        parts.append(severity)
    elif when:
        parts.append(f"on {when}")
    status = _status_clause(record)
    if status:
        parts.append(status)
    if record.get("value") is not None:
        parts.append(f"value {format_value(record['value'])}")
    policy = _policy_clause(record)
    if policy:
        parts.append(policy)
    if not parts:
        return UNREADABLE
    return ", ".join(parts) + "."


def reeval_panel(alert):
    """The Re-evaluation panel for one alert: a labelled line per record it carries.

    Both records can be present at once, on an alert re-scored at ingest and then
    re-scored again by an operator, so each line names the path that wrote it.
    """
    annotations = alert.annotations if isinstance(alert.annotations, dict) else {}
    rows = []
    for key, label in RECORD_LABELS:
        if key not in annotations:
            continue
        record = _load(annotations[key])
        rows.append((label, UNREADABLE if record is None else describe_record(record)))
    if not rows:
        return NEVER
    return format_html_join("", "<div><b>{}:</b> {}</div>", rows)
