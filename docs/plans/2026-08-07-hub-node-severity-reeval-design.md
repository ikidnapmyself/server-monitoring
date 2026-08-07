---
title: "Hub-side Per-Node Severity Re-evaluation"
parent: Plans
---

# Hub-side Per-Node Severity Re-evaluation

**Date:** 2026-08-07
**Status:** Approved (design)
**Apps:** `apps.alerts` (Node model, AlertProcessor), admin

## Problem

Severity is decided **node-side**: each checker computes a status and
`push_to_hub` maps it to an alert `severity` before pushing. That is uniform
across the fleet — but some nodes are legitimately different. A box that has run
at high CPU for years with no issue should not page at the default 90%; the
operator wants "only alert CPU > 99% **on this node**". Today there is no place
to express per-node policy.

## Approach (decided)

**Hub re-evaluates; nodes stay dumb.** Nodes keep sending raw `metrics` plus a
default severity. The hub stores per-node config and, at ingest, recomputes
severity against that node's thresholds, overriding the node-reported value.
No config is pushed down to nodes; no node change; the metrics the hub needs
already arrive in the cluster payload (`ClusterDriver` stashes them in
`annotations["metrics"]`).

**Unifying principle:** nodes report raw, granular data; the hub applies
per-node policy to re-decide severity. This generalizes beyond thresholds —
disk/path exclusions and port allowlists fit the same model later (the node
already reports per-mount / all-ports data; the hub filters at re-evaluation).
The one thing this model cannot do is change what a node physically scans
(needs push-down — deferred).

## Scope (first slice)

Per-node **numeric threshold** overrides for the seven numeric-threshold
checkers, plus the extension seam. Port allowlists, disk/dir exclusions, and
per-checker enable/disable are **follow-up slices** on the same storage +
dispatch seam — not built now (YAGNI; avoid the over-build pattern in
`docs/plans/2026-05-31-observability-overbuild-postmortem.md`).

## 1. Storage — general per-node/per-checker config

Add a `config` JSON field to `apps.alerts.models.Node` (default `dict`),
keyed by checker name → arbitrary per-checker config:

```json
{
  "cpu":       {"warning_threshold": 99, "critical_threshold": 99},
  "disk_temp": {"warning_threshold": 60, "critical_threshold": 68}
}
```

- Open-ended: future checker-types add their own keys (exclusions, allowlists)
  with no schema change.
- First slice reads only `warning_threshold` / `critical_threshold`.
- Managed via the `Node` **admin** (repo already uses `django_json_widget`).
- One migration (`Node.config`, default `{}`). Backward compatible: empty
  config = today's behavior.

## 2. Re-evaluation — hook + numeric evaluator

### Hook

`apps/alerts/services.py` — `AlertProcessor._process_alert`, **before**
`_create_alert` / `_update_alert` (which persist `severity=parsed.severity`).
This runs in the drain (`process_inbox` → `IngestExecutor` → `AlertProcessor`),
i.e. hub-side, after durable ingest and before notify.

### Public interface

```python
def reevaluate_severity(parsed: ParsedAlert) -> ParsedAlert:
    """Hub-side per-node severity override. Pure w.r.t. the DB read of Node;
    returns `parsed` unchanged when no per-node policy applies. Never raises."""
```

Algorithm:

1. `instance_id = parsed.labels.get("instance_id")`, `checker =
   parsed.labels.get("checker")`. Missing either ⇒ return `parsed` unchanged.
2. `node = Node.objects.filter(instance_id=instance_id).first()`; `cfg =
   (node.config or {}).get(checker)` if node else None. Absent ⇒ unchanged.
3. Dispatch: `evaluator = REEVALUATORS.get(checker)`; none ⇒ unchanged.
4. `metrics = json.loads(parsed.annotations["metrics"])` (guard
   missing/invalid). `value = metrics.get(PRIMARY_METRIC[checker])`; not a
   number ⇒ unchanged.
5. Recompute status from `cfg`:
   `value >= critical_threshold → CRITICAL`,
   `value >= warning_threshold → WARNING`, else `OK`.
6. Map → (severity, status):
   `CRITICAL → (critical, firing)`, `WARNING → (warning, firing)`,
   `OK → (info, resolved)`.
7. If changed, set `parsed.severity` / `parsed.status`, set `ended_at` when
   newly resolved, and record the audit annotation (§3).

### The `PRIMARY_METRIC` map (first slice)

| checker | metric key |
|---|---|
| `cpu` | `cpu_percent` |
| `memory` | `memory_percent` |
| `disk` | `worst_percent` |
| `disk_inodes` | `worst_percent` |
| `disk_temp` | `hottest_c` |
| `cpu_temp` | `hottest_c` |
| `io_strain` | `busiest_util_percent` |

### Dispatch seam

`REEVALUATORS: dict[str, Callable]` maps checker → evaluator. First slice
registers **one** numeric evaluator for the seven checkers above. Follow-up
checker-types (port allowlist, disk exclusions) register their own evaluator
later; the hook and storage do not change.

### Fail-open

Missing labels, no node, absent config, unknown checker, missing/invalid
`metrics`, non-numeric value → return `parsed` unchanged. Re-evaluation must
never raise into the ingest path.

### Threshold semantics

The node's override for a checker must provide both `warning_threshold` and
`critical_threshold` (the hub does not know the checker's node-side defaults).
To express "only alert at ≥ 99%", set both to 99 (below 99 → OK/resolved).

## 3. Audit, admin, tests

### Audit (AGENTS.md: record downgrades)

When severity changes, stash on the alert:

```json
"severity_reevaluated": {
  "from": "critical", "to": "info", "value": 95.2,
  "thresholds": {"warning_threshold": 99, "critical_threshold": 99},
  "by": "hub-node-policy"
}
```

and emit a log line (monitoring signal) with `instance_id`, `checker`,
from→to. The original node severity is preserved for traceability.

### Admin

`Node` admin exposes `config` (JSON widget) with inline help listing the seven
numeric checkers and their metric keys.

### Tests

`reevaluate_severity` unit tests:
- downgrade firing→resolved (hero: cpu 95% with node `crit=99`),
- upgrade (node said warning, node config `crit` lower → critical),
- no-config passthrough, unknown/non-numeric checker passthrough,
- missing `instance_id`/`checker` label passthrough,
- missing / malformed `annotations["metrics"]` passthrough,
- non-numeric metric value passthrough.

`AlertProcessor` integration test: a configured node's alert is **persisted**
with the re-evaluated severity/status; audit annotation present.

Target 100% branch coverage on changed code.

## Error handling & edge cases

- `node.config` missing key / `None` → treated as no policy.
- `metrics` absent or not valid JSON → passthrough.
- Value present but not int/float → passthrough.
- Re-evaluation is idempotent and side-effect-free apart from the single
  `Node` read.

## Acceptance criteria

- `Node.config` stores per-node/per-checker config; editable in admin.
- Hub re-evaluates severity for the seven numeric checkers against per-node
  thresholds at ingest; overrides node-reported severity/status.
- Nodes without config are unaffected (backward compatible).
- Overrides are audited (annotation + log).
- `black`/`ruff`/`bandit`/`pytest` clean; 100% branch coverage on changed code.

## Out of scope / follow-ups (same storage + seam)

- Per-node `listening_ports` allowlist re-evaluation.
- Per-node disk/directory exclusions.
- Per-checker enable/disable.
- Config push-down to nodes (only if noise volume ever justifies it).
- CLI to edit node config (admin is enough for the first slice).
