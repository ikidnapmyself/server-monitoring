---
title: "Per-node listening_ports allowlist re-evaluation"
parent: Plans
---

# Per-node `listening_ports` allowlist re-evaluation

Follow-up slice on the hub-side per-node policy seam established by
[`2026-08-07-hub-node-severity-reeval-design.md`](2026-08-07-hub-node-severity-reeval-design.md)
(ingest-time numeric override) and
[`2026-08-08-reeval-existing-alerts-design.md`](2026-08-08-reeval-existing-alerts-design.md)
(operator-triggered re-eval of existing open alerts).

## Motivation

Nodes audit their LISTENing sockets against a node-side
`settings.LISTENING_PORTS_ALLOWLIST` and push the full port inventory to the hub.
The hub should be able to apply a **per-node** allowlist policy and re-decide
whether a `listening_ports` alert is warranted — without touching the node.
Cluster-pushed alerts already carry the full `metrics["listening"]` list
(each socket's `port` / `exposed` flag) via `apps/alerts/drivers/cluster.py`, so
the hub can genuinely recompute flagged ports rather than trust the node's
`unexpected_ports`.

## Storage

`Node.config["listening_ports"] = {"allowlist": [22, 80, 443]}`.

No migration — the `config` JSON field already exists. Semantics mirror the
checker's `flagged_ports()`:

- **allowlist present and non-empty** → any listening port not in it is flagged.
- **`{"allowlist": []}`** → flag only externally-exposed (non-loopback) ports.
- **absent / malformed** → fail-open passthrough (alert unchanged).

## Scoring

New pure scorer in `apps/alerts/reevaluation.py`, same
`(checker, metrics, cfg) -> (severity, status, value) | None` shape as
`_score_numeric`:

```python
def _score_allowlist(checker, metrics, cfg):
    # cfg must be a dict; cfg["allowlist"] must be a list of ints  → else None
    # metrics["listening"] must be a list                          → else None
    # re-flag ports (mirrors checker.flagged_ports):
    #   port in allowlist            → ok
    #   else flagged if (allowlist non-empty) or entry["exposed"]
    #   malformed entry / bad port   → None (fail-open; never mis-resolve)
    # flagged → ("warning", "firing", float(count))
    # none    → ("info",    "resolved", 0.0)
```

`listening_ports` severity is binary (warning / ok — no critical), so the
mapping is warning→firing / ok→resolved, consistent with the numeric slice.

## Dispatch seam (both paths, one registry)

Today the config-change path (`reeval_existing._score_alert`) gates on
`REEVALUATORS` membership but hardcodes `_score_numeric`. To support a
non-numeric checker, both paths dispatch through a shared **pure-scorer**
registry:

```python
SCORERS = {
    **{c: _score_numeric for c in PRIMARY_METRIC},
    "listening_ports": _score_allowlist,
}
REEVALUATORS = {checker: _evaluate for checker in SCORERS}
```

- `_evaluate(parsed, cfg)` (ingest) extracts `metrics` + `checker` and calls
  `SCORERS[checker]`; replaces the per-checker `numeric_evaluator`.
- `reeval_existing._score_alert` calls `SCORERS[checker]` instead of always
  `_score_numeric`.

The ingest hook, storage, audit annotations, and incident auto-resolve are
unchanged.

## Behavior scope

For `listening_ports`, config-change re-eval only ever **resolves** a firing
alert (the allowlist now covers its ports) or no-ops — a still-flagged alert
stays `warning`/`firing`, so no change is recorded. This matches the numeric
slice's firing-only scope (`preview_node_alert_reeval` filters `status="firing"`;
resolved alerts are never re-fired via this path).

## Fail-open

Missing/malformed `cfg`, allowlist, or `metrics["listening"]`; a malformed port
entry; or any exception → alert passed through unchanged. Re-evaluation must
never raise into the ingest path.

## Tests

- Scorer units: allowlist covers all ports → resolved; some outside → firing;
  empty allowlist → exposed-only; malformed cfg / metrics / entry → None.
- Ingest path (`reevaluate_severity`): a `listening_ports` alert resolves when
  the node allowlist covers its ports; audited in `annotations["severity_reevaluated"]`.
- Config-change path (`preview` / `apply`): resolves, records `AlertHistory` +
  `reevaluated_on_config_change`, auto-resolves the incident.
- Numeric checkers unchanged after the registry refactor.
- 100% branch coverage on changed lines.

## Docs

Update the `apps/alerts/AGENTS.md` re-eval note to mention the allowlist
evaluator and the `SCORERS` seam.
