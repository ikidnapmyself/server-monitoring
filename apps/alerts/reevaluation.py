"""Hub-side per-node severity re-evaluation.

Nodes report raw metrics + a default severity; the hub recomputes severity
against per-node policy stored in Node.config. Fail-open: any missing/invalid
input returns the alert unchanged. Never raises into the ingest path.

See docs/plans/2026-08-07-hub-node-severity-reeval-design.md.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from apps.alerts.drivers.base import ParsedAlert
from apps.alerts.metrics import parse_metrics

logger = logging.getLogger(__name__)

# parse_metrics now LIVES in apps.alerts.metrics — apps.alerts.context_keys needs the
# same "read a node's metrics back out of annotations" rule, and importing it from here
# would couple the fan-out gate to the severity re-evaluator. It stays exported from this
# module so the existing callers that import it from here keep working.
__all__ = [
    "parse_metrics",
    "reevaluate_severity",
    "SCORERS",
    "REEVALUATORS",
    "Verdict",
    "Skip",
    "SkipReason",
]


class SkipReason(str, Enum):
    """Why a re-evaluation produced no verdict.

    Every value is a passthrough at ingest. They differ only where a human asked
    the question and is owed an answer.
    """

    NO_SCORER = "no_scorer"
    NO_POLICY = "no_policy"
    MALFORMED_POLICY = "malformed_policy"
    INCOMPLETE_THRESHOLDS = "incomplete_thresholds"
    INVERTED_THRESHOLDS = "inverted_thresholds"
    NO_PRIMARY_METRIC = "no_primary_metric"
    NO_METRICS = "no_metrics"
    NO_METRIC_VALUE = "no_metric_value"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class Verdict:
    """A score the caller may act on."""

    severity: str
    status: str
    value: float


@dataclass(frozen=True)
class Skip:
    """No score, and why. ``context`` carries what the sentence needs."""

    reason: SkipReason
    context: dict

    def __init__(self, reason: SkipReason, **context):
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "context", context)


Outcome = Verdict | Skip


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
    return parse_metrics(parsed.annotations)


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _score_numeric(checker: str, metrics: dict, cfg) -> Outcome:
    """Pure scorer shared by ingest and config-change re-evaluation.

    `_number` rejects bool (a subclass of int) and non-numbers. An inverted config
    (critical below warning) is malformed. Every failure is a ``Skip``, which every
    caller treats as passthrough, so this stays fail-open.
    """
    if cfg is None:
        return Skip(SkipReason.NO_POLICY, checker=checker)
    if not isinstance(cfg, dict):
        return Skip(SkipReason.MALFORMED_POLICY, checker=checker)
    warn = _number(cfg.get("warning_threshold"))
    crit = _number(cfg.get("critical_threshold"))
    if warn is None or crit is None:
        return Skip(SkipReason.INCOMPLETE_THRESHOLDS, checker=checker)
    if crit < warn:
        return Skip(SkipReason.INVERTED_THRESHOLDS, warning=warn, critical=crit)
    metric_key = PRIMARY_METRIC.get(checker)
    if metric_key is None:
        return Skip(SkipReason.NO_PRIMARY_METRIC, checker=checker)
    if not isinstance(metrics, dict):
        return Skip(SkipReason.NO_METRICS, checker=checker)
    value = _number(metrics.get(metric_key))
    if value is None:
        return Skip(SkipReason.NO_METRIC_VALUE, metric_key=metric_key)
    if value >= crit:
        return Verdict("critical", "firing", value)
    if value >= warn:
        return Verdict("warning", "firing", value)
    return Verdict("info", "resolved", value)


def _int_set(values) -> set[int] | None:
    """Coerce a list of numbers to a set of ints; None if any element is invalid.

    `_number` rejects bool and non-numbers, so a string/bool port fails open
    (passthrough) rather than being silently coerced.
    """
    result: set[int] = set()
    for value in values:
        number = _number(value)
        if number is None:
            return None
        result.add(int(number))
    return result


def _flag_ports(listening: list, allowset: set[int]) -> list[int] | None:
    """Ports violating policy, mirroring the checker's ``flagged_ports``.

    With an allowlist: every port not in it. Without one (empty allowlist): only
    externally-exposed ports. A malformed entry returns None so callers fail open
    (never mis-resolve on bad data).
    """
    flagged: list[int] = []
    for entry in listening:
        if not isinstance(entry, dict):
            return None
        port = _number(entry.get("port"))
        if port is None:
            return None
        if int(port) in allowset:
            continue
        if allowset or entry.get("exposed"):
            flagged.append(int(port))
    return flagged


def _score_allowlist(checker: str, metrics: dict, cfg) -> tuple[str, str, float] | None:
    """Re-flag listening ports against a per-node allowlist. Binary warning/ok.

    Reuses the checker's own flagging semantics against the full ``listening``
    inventory the node reports. Returns None for any missing/invalid input so
    callers can fail open. ``checker`` is unused (uniform scorer signature).
    """
    if not isinstance(cfg, dict) or not isinstance(metrics, dict):
        return None
    allow = cfg.get("allowlist")
    if not isinstance(allow, list):
        return None
    allowset = _int_set(allow)
    if allowset is None:
        return None
    listening = metrics.get("listening")
    if not isinstance(listening, list):
        return None
    flagged = _flag_ports(listening, allowset)
    if flagged is None:
        return None
    count = float(len(flagged))
    if flagged:
        return ("warning", "firing", count)
    return ("info", "resolved", count)


def numeric_evaluator(parsed: ParsedAlert, cfg: dict) -> Outcome:
    """Score a numeric checker from the alert's stored metrics."""
    metrics = _metrics(parsed)
    checker = (parsed.labels or {}).get("checker", "")
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker=checker)
    return _score_numeric(checker, metrics, cfg)


def allowlist_evaluator(parsed: ParsedAlert, cfg: dict) -> tuple[str, str, float] | None:
    """Return (severity, status, value) for listening_ports, or None to passthrough."""
    metrics = _metrics(parsed)
    if metrics is None:
        return None
    return _score_allowlist("listening_ports", metrics, cfg)


# Pure-scorer dispatch: checker -> (checker, metrics, cfg) -> Outcome. The union in
# the annotation covers `_score_allowlist`, which still returns a tuple or None.
# Shared by ingest (via the evaluators below) and config-change re-eval
# (`apps.alerts.reeval_existing`), so both paths score a checker identically.
# cfg is typed `object`: each scorer validates it (fail-open on a non-dict), and
# callers pass a raw `Node.config[checker]` lookup that may be None/malformed.
SCORERS: dict[str, Callable[[str, dict, object], "Outcome | tuple[str, str, float] | None"]] = {
    **{checker: _score_numeric for checker in PRIMARY_METRIC},
    "listening_ports": _score_allowlist,
}

# Ingest dispatch: checker -> evaluator(parsed, cfg) -> Outcome, with the same
# tuple-or-None union still covering `allowlist_evaluator`.
REEVALUATORS: dict[
    str, Callable[[ParsedAlert, dict], "Outcome | tuple[str, str, float] | None"]
] = {
    **{checker: numeric_evaluator for checker in PRIMARY_METRIC},
    "listening_ports": allowlist_evaluator,
}


def _reevaluate(parsed: ParsedAlert) -> ParsedAlert:
    """Core re-evaluation logic; may raise. Wrapped by reevaluate_severity."""
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
    if not isinstance(cfg, dict) or not cfg:
        return parsed

    outcome = evaluator(parsed, cfg)
    # `allowlist_evaluator` still returns a bare tuple, so both shapes score here.
    if isinstance(outcome, Verdict):
        severity, status, value = outcome.severity, outcome.status, outcome.value
    elif isinstance(outcome, tuple):
        severity, status, value = outcome
    else:
        return parsed
    if severity == parsed.severity and status == parsed.status:
        return parsed

    original_severity = parsed.severity
    original_status = parsed.status
    parsed.annotations = dict(parsed.annotations or {})
    parsed.annotations["severity_reevaluated"] = json.dumps(
        {
            "from": original_severity,
            "to": severity,
            "status_from": original_status,
            "status_to": status,
            "value": value,
            "thresholds": cfg,
            "checker": checker,
            "by": "hub-node-policy",
        }
    )
    # Keep ended_at consistent with the re-evaluated status in both directions.
    if status == "firing":
        parsed.ended_at = None
    elif parsed.ended_at is None:
        from django.utils import timezone

        parsed.ended_at = timezone.now()
    parsed.severity = severity
    parsed.status = status
    logger.info(
        "Re-evaluated severity for %s on %s: %s -> %s",
        checker,
        instance_id,
        original_severity,
        severity,
    )
    return parsed


def reevaluate_severity(parsed: ParsedAlert) -> ParsedAlert:
    """Override severity/status from the node's per-checker policy.

    Returns ``parsed`` unchanged when no policy applies. Fail-open: any exception
    is logged and the alert is passed through untouched, so re-evaluation can never
    raise into the ingest path (which would roll back the whole webhook batch).
    """
    try:
        return _reevaluate(parsed)
    except Exception:  # noqa: BLE001 - fail-open contract: never raise into ingest
        labels = getattr(parsed, "labels", None)
        if isinstance(labels, dict):
            ctx = f"{labels.get('instance_id')}/{labels.get('checker')}"
        else:
            ctx = "?"
        logger.exception("severity re-evaluation failed for %s; passing through", ctx)
        return parsed
