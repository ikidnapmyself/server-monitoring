---
title: "Re-evaluate Existing Alerts on Node Config Change"
parent: Plans
---

# Re-evaluate Existing Alerts on Node Config Change

**Date:** 2026-08-08
**Status:** Approved (design)
**App:** `apps.alerts` (service + admin action + management command)

## Problem

The hub-side per-node severity re-evaluation (PR #197) only affects **incoming**
alerts. When an operator changes a node's thresholds — e.g. sets "only alert CPU
> 99%" on a box that runs hot — the alerts that were *already firing* under the
old thresholds stay firing. The operator has to hunt them down and resolve them
by hand. We want a config change to be able to re-score the node's existing open
alerts and resolve/adjust the ones that no longer fit the new policy — **behind a
confirmation step** so nothing changes silently.

## Approach (decided)

A shared core function, exposed through **two operator-triggered surfaces** (both
chosen): a Django **admin action with a confirmation preview**, and a
**management command** with `--dry-run` + a confirm prompt. Both preview first,
apply only on confirm.

**Full re-score** (decided): each open alert gets the complete new outcome —
resolved if it no longer breaches, or an updated severity if it still fires
(critical→warning, or warning→critical if thresholds were lowered).

**Scope boundary:** this operates on **existing open (firing) alerts only**. It
does not create new alerts or re-open resolved ones for lowered thresholds — that
would require re-running checks, not re-scoring stored results.

## 1. Shared core

New module `apps/alerts/reeval_existing.py`:

```python
@dataclass
class AlertChange:
    alert: Alert
    old_severity: str
    old_status: str
    new_severity: str
    new_status: str
    value: float

@dataclass
class ReevalReport:
    node: Node
    changes: list[AlertChange]          # only alerts whose outcome changes
    resolved_count: int
    severity_changed_count: int

def preview_node_alert_reeval(node: Node) -> ReevalReport: ...
def apply_node_alert_reeval(node: Node) -> ReevalReport: ...   # transaction + writes
```

Selection: `Alert.objects.filter(node=node, status="firing")`, then keep those
whose `labels["checker"]` is in `REEVALUATORS` (numeric), that have parseable
`annotations["metrics"]`, and for which `node.config` has that checker.

Scoring (DRY): extract a pure `_score_numeric(checker, metrics, cfg) ->
(severity, status, value) | None` in `reevaluation.py`; `numeric_evaluator`
(ingest) becomes a thin wrapper that pulls `(checker, metrics)` from a
`ParsedAlert`, and this feature pulls `(checker, metrics)` from a persisted
`Alert`. Both call the same function — one source of truth for the math.

`preview_*` computes changes without writing. `apply_*` wraps the writes in a
`transaction.atomic()`:
- For each changed alert: set `severity`, `status`, `ended_at` (None when firing,
  `timezone.now()` when newly resolved), save; write an `AlertHistory` row
  (`event="reevaluated"` / `"resolved"`); stamp the audit annotation (§3).
- After alert writes, **reuse the existing incident auto-resolve** logic
  (mirror `AlertOrchestrator._check_incident_resolution`, scoped to this node's
  incidents): resolve any open/acknowledged incident whose alerts are now all
  resolved.

Idempotent: re-running produces an empty `changes` list.

## 2. Admin surface — confirmation dialog

`NodeAdmin` gains the `DjangoObjectActions` mixin and a `reevaluate_open_alerts`
object-action (button on the Node change page, same pattern as `IncidentAdmin`'s
resolve/acknowledge):

- **Click (GET):** run `preview_node_alert_reeval(node)`, render a confirmation
  template listing each affected alert (checker, `old_severity/old_status →
  new_severity/new_status`, value, thresholds), with a **Confirm** POST button.
  Empty report → message "No open alerts need re-evaluation."
- **Confirm (POST):** run `apply_node_alert_reeval(node)`, redirect back to the
  Node change page with `messages.success("Resolved N, changed severity on M")`.

Nothing changes until Confirm — this is the "confirmation dialog".

## 3. CLI surface + audit

`apps/alerts/management/commands/reevaluate_node_alerts.py`:

```
manage.py reevaluate_node_alerts <instance_id>            # preview, then prompt Apply? [y/N]
manage.py reevaluate_node_alerts <instance_id> --dry-run  # preview only, no prompt
manage.py reevaluate_node_alerts <instance_id> --noinput  # apply without prompting
```

Prints the preview table (checker, old→new, value, thresholds). Unknown
`instance_id` → `CommandError`. Reuses the same core functions.

### Audit — a distinct key

Each changed alert records, separately from the ingest-time
`severity_reevaluated`, a **`reevaluated_on_config_change`** annotation:

```json
{"from": "critical", "to": "info", "status_from": "firing", "status_to": "resolved",
 "value": 95.2, "thresholds": {"warning_threshold": 99, "critical_threshold": 99},
 "checker": "cpu", "by": "hub-node-policy:config-change", "at": "<iso8601>"}
```

plus an `AlertHistory` row. This keeps operator-triggered re-evals
distinguishable from ingest-time ones in the trail.

## Error handling & edge cases

- Alert missing/invalid `annotations["metrics"]`, no `node.config[checker]`,
  non-numeric checker, or unchanged outcome → skipped (not in `changes`).
- `apply_*` is transactional; a failure rolls back the batch.
- No open alerts / nothing changes → empty report; admin + CLI say so.
- Malformed config value → `_score_numeric` returns None (fail-open, per PR #197).

## Tests

Core (`reeval_existing`): resolve (95% under crit=99), downgrade
(critical→warning), upgrade (warning→critical after lowering), skips
(no metrics / no config / non-numeric / unchanged), incident auto-resolves when
its last alert resolves, idempotent re-run, transactional apply. Admin: preview
renders affected alerts; confirm applies + messages; empty case. CLI: `--dry-run`
previews without writing; apply path (with `--noinput`) mutates; unknown
instance_id errors. 100% branch coverage on new code.

## Acceptance criteria

- Operator can preview and (on confirm) re-score a node's open alerts against its
  current `config`, from both admin and CLI.
- Full re-score applied: resolutions + severity changes; incidents auto-resolve.
- Distinct `reevaluated_on_config_change` audit annotation + `AlertHistory`.
- Existing-firing-only scope; no new/re-opened alerts.
- `black`/`ruff`/`bandit`/`pytest` clean; 100% branch coverage on new code.

## Out of scope / follow-ups

- Auto-triggering on `Node.config` save (kept explicit/operator-triggered).
- Re-running checks to create new alerts for newly-breaching metrics.
- The other per-node policy slices (listening_ports allowlist, disk exclusions,
  enable/disable) — this re-eval-on-change mechanism will apply to them too once
  their evaluators exist.
