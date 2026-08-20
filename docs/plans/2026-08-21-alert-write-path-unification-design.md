---
title: "Alert Write-Path Unification and Incident Reopen"
parent: Plans
---

# Alert write-path unification and incident reopen

**Status:** design approved 2026-08-21. Follows incident fan-out
(`docs/plans/2026-08-19-incident-fanout-design.md`, merged as PR #208), which left both of these
open.

---

## 1. The problem

### 1.1 Two write paths that keep drifting

`AlertOrchestrator` and `CheckAlertBridge` are separate create/update/resolve implementations over
the same `Alert` rows. Only *grouping* was ever unified (the bridge delegates to
`AlertOrchestrator._create_or_attach_incident`). The bridge still owns its own `_create_alert`,
`_update_alert`, `_resolve_alert` and `_check_incident_resolution`.

This duplication has now caused **three separate defects**, each found by a different means:

1. `ProcessingResult.alerts` was added to the orchestrator during the routing simplification;
   `CheckAlertResult.alerts` stayed permanently empty. 100% branch coverage did not catch it — the
   `extend` line executes, it just extends an empty list. **Coverage measures execution, not
   effect.**
2. Incident fan-out added materiality recording to the orchestrator; the bridge needed it in
   *three* places, one of which (`_resolve_alert`) the implementation plan forgot entirely.
3. The bridge's `_update_alert` never restored `status`/`ended_at` on a resolved→firing refire.
   Latent for a long time, then dangerous: the alert's stored status is a **routing fact**, so the
   seeded `resolved-all-clear` lane delivered an all-clear for a CRITICAL problem. Found by an
   external reviewer on PR #208, not by the suite.

A fourth divergence exists today and has not yet bitten: the two `_check_incident_resolution`
implementations differ — the orchestrator's calls `incident.resolve(summary=...)` and guards on
`alerts.exists()`; the bridge's writes the fields directly, sets no summary, and would resolve an
incident that has no alerts at all.

The pattern is not bad luck. Anything added to one path silently does nothing on the other, and
the checker path is the one carrying node traffic.

### 1.2 A refire does not reopen its incident

`_create_or_attach_incident` runs only from `_create_alert`. On a refire — the update path — no
incident logic runs at all, on **either** path. `Alert` rows are reused per fingerprint, so the
alert keeps its FK to an incident that is still `RESOLVED`, and `_find_open_incident` only
considers OPEN/ACKNOWLEDGED, so nothing ever revisits it.

The result an operator sees: a FIRING alert hanging off a RESOLVED incident. PR #208 fixed the
alert half of this (the row now reopens); the incident half is this design.

---

## 2. The shape

### 2.1 One write path

`CheckAlertBridge._process_alert` shrinks to a shell holding the single rule that genuinely
differs, then delegates:

```python
def _process_alert(self, parsed, source, result):
    # The one checker-specific rule: an OK result for a fingerprint we have never
    # alerted on is not news. Without this guard every healthy checker would create
    # a resolved Alert row on its first run.
    if parsed.status != "firing" and not Alert.objects.filter(
        fingerprint=parsed.fingerprint, source=source
    ).exists():
        return None
    return self.orchestrator._process_alert(parsed, source, result)
```

`_create_alert`, `_update_alert`, `_resolve_alert` and `_check_incident_resolution` are **deleted**
from the bridge — four methods, ~110 lines. No new wiring is needed: the bridge already holds a
fully configured `AlertOrchestrator` (`check_integration.py:118`) carrying `auto_create_incidents`,
`auto_resolve_incidents` and `trace_id`.

`check_result_to_parsed_alert` stays: converting a `CheckResult` into a `ParsedAlert` is the
bridge's actual job, and it is what makes delegation possible.

**Why wholesale rather than shared helpers.** Two thin shells calling shared functions can still
drift in *which* helpers they call and *when* — which is exactly how defect 2 above happened. One
path removes the failure mode rather than documenting it.

### 2.2 Reopen on refire

`Incident.reopen()` joins the existing `resolve()` / `close()` lifecycle methods: status back to
OPEN, `resolved_at` and `closed_at` cleared.

It is called from `AlertOrchestrator._update_alert`, at the refire branch that already detects
`resolved → firing`. Because of §2.1 that is now the only such branch in the codebase, so checker
traffic gets it without a line being written in the bridge.

The rule, in full:

```
resolved --refire--> open
closed   --refire--> open
open     --refire--> open   (no change)
acked    --refire--> acked  (no change: not an end state)
```

CLOSED reopens too. An operator's close is a deliberate final word, but a thing firing again is
new evidence, and the alternative leaves the exact mismatch this design exists to remove. One rule
also means nothing to remember.

The reopen is recorded so the merged incident timeline (`apps/alerts/timeline.py`) shows it.

---

## 3. Behaviour changes

All four are on checker traffic only, and none is silent:

1. History events become `updated` / `refired` / `resolved` with the orchestrator's
   `{"changed": ...}` diff, instead of `severity_changed`. Nothing in production code reads
   `severity_changed` — only one bridge test asserts it. (`reeval_existing` writes its own events
   and keeps its own counter; it is unaffected.)
2. An update now also writes `name` and `labels`, so `metric_*` label churn appears in the diff
   detail. Cosmetic: materiality does not read the diff.
3. `alerts_updated` counts only non-status-change updates, matching the webhook path.
4. Auto-resolution sets the summary "All alerts resolved automatically" and no longer resolves an
   alert-less incident.

The incident-creation gate *looks* different between the paths — the bridge gates on
`severity in (critical, warning)`, the orchestrator on `status == "firing"` — but is equivalent in
practice: `CRITICAL`/`WARNING`/`UNKNOWN` all map to firing with a non-info severity, and `OK` maps
to resolved with info severity (`STATUS_TO_SEVERITY` / `STATUS_TO_ALERT_STATUS`).

---

## 4. Testing

Parity stops being something to test and becomes structural — there is one path. What is worth
testing:

- **The checker-specific guard:** an OK result for an unknown fingerprint creates nothing.
- **The reopen matrix:** RESOLVED → OPEN, CLOSED → OPEN, OPEN and ACKNOWLEDGED untouched.
- **The reopen is recorded**, so the timeline shows it.
- **End to end** (`test_fanout_e2e.py`): fire → resolve → refire produces a downstream run that
  does *not* take `resolved-all-clear`, and leaves the incident OPEN. This is the acceptance
  criterion for the original bug.

Existing bridge tests that assert deleted internals are rewritten against the unified behaviour,
never weakened to pass.

---

## 5. Risk

This touches the path **all** checker traffic takes — the hub's own cron and every node push.
What makes it tolerable: 43 existing bridge tests plus the fan-out acceptance module already pin
the observable outcomes; the four deltas are enumerated above and none is silent; 100% branch
coverage on changed code is enforced; and each task is one commit, individually revertable.

---

## 6. Out of scope, deliberately

- **The legacy resume fallback** in `orchestrator.resume_pipeline` — kept this round. It is only
  reachable by runs that failed before the fan-out deploy, and deleting it early means any such
  run silently loses its downstream work when retried.
- **Grouping by `Alert.fingerprint`** (the source's own dedup key), `_find_open_incident`'s
  unordered `.first()`, and incident severity never de-escalating. All pre-existing.

---

## 7. Acceptance

- The bridge has no `_create_alert`, `_update_alert`, `_resolve_alert` or
  `_check_incident_resolution`.
- A refire reopens its incident on both ingest paths, and the downstream run it enqueues routes as
  firing.
- An OK check for a fingerprint the hub has never alerted on still creates nothing.
- Full suite green; 100% branch coverage on changed code.
