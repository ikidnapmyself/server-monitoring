"""Hub-side per-node severity re-evaluation.

Nodes report raw metrics + a default severity; the hub recomputes severity
against per-node policy stored in Node.config. Fail-open: any missing/invalid
input returns the alert unchanged. Never raises into the ingest path.

See docs/plans/2026-08-07-hub-node-severity-reeval-design.md.
"""

import json
import logging
from collections.abc import Callable

from apps.alerts.drivers.base import ParsedAlert

logger = logging.getLogger(__name__)

# checker -> the metric key carrying its primary numeric value
PRIMARY_METRIC = {
    "cpu": "cpu_percent",
    "memory": "memory_percent",
    "disk": "worst_percent",
    "disk_inodes": "worst_percent",
    "disk_temp": "hottest_c",
    "cpu_temp": "hottest_c",
    "io_strain": "busiest_util_percent",
}


def _metrics(parsed: ParsedAlert) -> dict | None:
    raw = (parsed.annotations or {}).get("metrics")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def numeric_evaluator(parsed: ParsedAlert, cfg: dict) -> tuple[str, str] | None:
    """Return (severity, status) for a numeric checker, or None to passthrough."""
    warn = cfg.get("warning_threshold")
    crit = cfg.get("critical_threshold")
    if not isinstance(warn, (int, float)) or not isinstance(crit, (int, float)):
        return None
    checker = (parsed.labels or {}).get("checker", "")
    metrics = _metrics(parsed)
    if metrics is None:
        return None
    value = _number(metrics.get(PRIMARY_METRIC[checker]))
    if value is None:
        return None
    if value >= crit:
        return ("critical", "firing")
    if value >= warn:
        return ("warning", "firing")
    return ("info", "resolved")


# Dispatch seam: checker -> evaluator(parsed, cfg) -> (severity, status) | None.
# First slice: one numeric evaluator for the seven numeric checkers.
REEVALUATORS: dict[str, Callable[[ParsedAlert, dict], "tuple[str, str] | None"]] = {
    checker: numeric_evaluator for checker in PRIMARY_METRIC
}


def reevaluate_severity(parsed: ParsedAlert) -> ParsedAlert:
    """Override severity/status from the node's per-checker policy.

    Returns ``parsed`` unchanged when no policy applies. Never raises.
    """
    labels = parsed.labels or {}
    checker = labels.get("checker")
    instance_id = labels.get("instance_id")
    if not checker or not instance_id:
        return parsed

    evaluator = REEVALUATORS.get(checker)
    if evaluator is None:
        return parsed

    from apps.alerts.models import Node

    node = Node.objects.filter(instance_id=instance_id).first()
    if node is None:
        return parsed
    cfg = (node.config or {}).get(checker)
    if not cfg:
        return parsed

    outcome = evaluator(parsed, cfg)
    if outcome is None:
        return parsed

    severity, status = outcome
    if severity == parsed.severity and status == parsed.status:
        return parsed

    original = parsed.severity
    parsed.annotations = dict(parsed.annotations or {})
    parsed.annotations["severity_reevaluated"] = json.dumps(
        {"from": original, "to": severity, "checker": checker, "by": "hub-node-policy"}
    )
    if status == "resolved" and parsed.status != "resolved" and parsed.ended_at is None:
        from django.utils import timezone

        parsed.ended_at = timezone.now()
    parsed.severity = severity
    parsed.status = status
    logger.info(
        "Re-evaluated severity for %s on %s: %s -> %s",
        checker,
        instance_id,
        original,
        severity,
    )
    return parsed
