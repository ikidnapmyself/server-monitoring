"""Per-checker "what situation is this?" keys for the fan-out change gate.

The gate must not compare free text: for checker traffic ``description`` is
``CheckResult.message`` (``apps/alerts/check_integration.py:158``), which carries
live metric values and changes on nearly every push. Severity and status alone are
too coarse for some checkers — a new unexpected port at unchanged WARNING is real
news — so those checkers name the part of their metrics that identifies the
situation.

This is a hub-side registry, NOT a method on ``BaseChecker``: checkers run on nodes,
and the hub only ever sees the resulting ``Alert``. It mirrors
``apps.alerts.reevaluation.SCORERS`` and needs no node-side change.

Two producers write metrics into annotations and both are read here:

* ``apps.alerts.drivers.cluster`` (node push) stores the whole metrics dict as a
  JSON string under ``annotations["metrics"]`` — what ``parse_metrics`` reads.
* ``apps.alerts.check_integration`` (hub-local checker runs) writes one
  ``str(value)`` annotation per metric key and no ``metrics`` blob at all, so a
  list metric arrives as its ``repr`` (``"[8080, 22]"``, which is also valid JSON).
"""

import hashlib
import json
import logging
from collections.abc import Callable

from apps.alerts.reevaluation import parse_metrics

logger = logging.getLogger(__name__)

#: Longest human-readable key we store verbatim. ``Alert.context_key`` is a
#: ``CharField(max_length=255)``; beyond this bound the key is digested rather than
#: truncated, because two port sets sharing a prefix must not collapse to one key —
#: that would silently skip a downstream run for a genuinely new situation.
MAX_PLAIN_KEY_LENGTH = 200


def _as_list(value: object) -> list | None:
    """Coerce a metric value to a list, accepting both annotation shapes.

    Elements are left as decoded; the caller decides which of them are ports.

    Returns None (→ no key) for anything that is not a list or a JSON string
    encoding one.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, list) else None
    return None


def _bound(joined: str) -> str:
    """Keep the key inside the column without losing injectivity."""
    if len(joined) <= MAX_PLAIN_KEY_LENGTH:
        return joined
    return f"sha256:{hashlib.sha256(joined.encode()).hexdigest()}"


def _listening_ports_key(metrics: dict) -> str:
    """The sorted set of flagged ports. Empty when nothing is flagged."""
    ports = _as_list(metrics.get("unexpected_ports"))
    if ports is None:
        return ""
    numbers = sorted({p for p in ports if isinstance(p, int) and not isinstance(p, bool)})
    return _bound(",".join(str(p) for p in numbers))


#: checker name -> (metrics) -> key. A checker with no entry has no key, which means
#: severity and status alone decide whether its re-push is material.
CONTEXT_KEYS: dict[str, Callable[[dict], str]] = {
    "listening_ports": _listening_ports_key,
}


def context_key_for(checker: str, annotations: object) -> str:
    """Stable key for this alert's situation, or "" when there is nothing to compare.

    Fails **open** (returns ""): a checker with no entry, unparseable annotations or a
    raising builder all degrade to severity/status-only gating, which over-notifies
    rather than silencing. Silence is the dangerous direction here.
    """
    builder = CONTEXT_KEYS.get(checker or "")
    if builder is None:
        return ""
    if not isinstance(annotations, dict):
        return ""
    metrics = parse_metrics(annotations)
    if not isinstance(metrics, dict):
        # No (or unparseable) metrics blob: the flat per-key annotation shape.
        metrics = annotations
    try:
        return builder(metrics)
    except Exception:  # noqa: BLE001 - fail-open contract: never raise into ingest
        logger.exception("context_key builder failed for checker %r", checker)
        return ""
