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
  ``str(value)`` annotation per metric key and no ``metrics`` blob at all, so
  values arrive as their ``repr``.

``_normalized_metrics`` reconciles the two so builders only ever see decoded
values; a builder should never have to ask which producer wrote the alert.

Known limitation — the key reflects the *node's* verdict
--------------------------------------------------------
``_listening_ports_key`` reads ``metrics["unexpected_ports"]``, which is the node's
flagged set computed against the node's own local allowlist. Hub-side allowlist
re-evaluation (``apps.alerts.reevaluation._score_allowlist``) independently
recomputes flagged ports from ``metrics["listening"]`` against
``Node.config["listening_ports"]["allowlist"]`` and does not write its result back
into the metrics, so the two hub-side registries can disagree: a port the hub has
deliberately allowlisted still moves the context key. The consequence is a possible
spurious downstream run, never a silenced one, so this is left alone here — the fix
belongs with the re-evaluation owner.
"""

import hashlib
import json
import logging
from collections.abc import Callable

from apps.alerts.metrics import parse_metrics

logger = logging.getLogger(__name__)

#: Longest human-readable key we store verbatim. ``Alert.context_key`` is a
#: ``CharField(max_length=255)``; beyond this bound the key is digested rather than
#: truncated, because two port sets sharing a prefix must not collapse to one key —
#: that would silently skip a downstream run for a genuinely new situation.
MAX_PLAIN_KEY_LENGTH = 200


def _decode(value: object) -> object:
    """Best-effort JSON-decode of a stringified metric value.

    ``"[8080, 22]"`` → ``[8080, 22]`` and ``"91.2"`` → ``91.2``, while plain text
    such as ``"web-01"`` is returned unchanged.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _normalized_metrics(annotations: dict) -> dict:
    """One shape for builders: decoded values, whichever producer wrote them."""
    nested = parse_metrics(annotations)
    if nested is not None:
        return nested
    return {key: _decode(value) for key, value in annotations.items()}


def _fit_to_column(key: str) -> str:
    """Keep the key inside ``Alert.context_key`` without losing injectivity.

    Applied centrally in :func:`context_key_for`, so the bound is an invariant of
    this module rather than something each builder has to remember. Idempotent for
    any key already within the bound.
    """
    if len(key) <= MAX_PLAIN_KEY_LENGTH:
        return key
    return f"sha256:{hashlib.sha256(key.encode()).hexdigest()}"


def _listening_ports_key(metrics: dict) -> str:
    """The sorted set of flagged ports, namespaced by checker.

    A clean scan yields ``"listening_ports:"`` — a real situation — which must not
    be confused with ``""``, meaning this module has nothing to compare.
    """
    ports = metrics.get("unexpected_ports")
    if not isinstance(ports, list):
        return ""
    numbers = sorted({p for p in ports if isinstance(p, int) and not isinstance(p, bool)})
    return "listening_ports:" + ",".join(str(p) for p in numbers)


#: checker name -> (metrics) -> key. A checker with no entry has no key, which means
#: severity and status alone decide whether its re-push is material.
KEY_BUILDERS: dict[str, Callable[[dict], str]] = {
    "listening_ports": _listening_ports_key,
}


def context_key_for(checker: str, annotations: object) -> str:
    """Stable key for this alert's situation, or "" when there is nothing to compare.

    Fails **open** (returns ""): a checker with no entry, unparseable annotations or a
    raising builder all degrade to severity/status-only gating, which over-notifies
    rather than silencing. Silence is the dangerous direction here.
    """
    builder = KEY_BUILDERS.get(checker or "")
    if builder is None:
        return ""
    if not isinstance(annotations, dict):
        return ""
    metrics = _normalized_metrics(annotations)
    try:
        return _fit_to_column(str(builder(metrics)))
    except Exception:  # noqa: BLE001 - fail-open contract: never raise into ingest
        logger.exception("context_key builder failed for checker %r", checker)
        return ""
