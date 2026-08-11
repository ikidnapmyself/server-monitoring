---
title: "Alert history: record every re-fire with a change diff"
parent: Plans
---

# Alert history: record every re-fire with a change diff

## Problem

On the admin alert-change page (`admin/alerts/alert/<id>/change/`), an alert that has
been firing continuously for days shows only a single `AlertHistory` row
(`event="created"`). The repeated firings leave no trail.

### Root cause

`AlertHistory` rows are written in only two places:

1. `_create_alert` — one `event="created"` row (`apps/alerts/services.py:260`).
2. `_update_alert` — a row **only** when the status changes
   (`parsed.status != old_status`, `apps/alerts/services.py:294-311`): firing→resolved
   (`"resolved"`) or resolved→firing (`"refired"`).

When a webhook arrives for an already-firing alert and the status is unchanged, control
falls into the `else` branch (`apps/alerts/services.py:314-315`):

```python
else:
    result.alerts_updated += 1
alert.save()
```

That bumps fields and `updated_at` but writes **no history row**. An alert that fires
continuously and never flips to resolved and back therefore keeps a single `"created"`
row forever. This is by design, not data loss — but it hides ongoing activity.

## Decision

Record **every re-fire as its own `AlertHistory` row** (full fidelity), and capture
**what changed** in `details`. Chosen over a heartbeat/counter approach because the user
wants a complete per-webhook trail.

## Design

The single behavioral change is in `_update_alert` (`apps/alerts/services.py`). The
status-unchanged `else` branch now always writes one row per received webhook.

### New row semantics (status unchanged)

- `event = "updated"`
- `old_status = new_status = alert.status` (both `"firing"`)
- `details = {"changed": {<field>: [old, new], ...}}` — the diff of meaningful fields;
  `{"changed": {}}` when nothing changed (still a timestamped "still firing" marker).

### Fields diffed

The diff compares the **existing** alert against the incoming `parsed` data, computed
**before** the field assignments at `apps/alerts/services.py:286-291` overwrite the old
values.

| Field | Diffed? | Rationale |
|---|---|---|
| `severity` | yes | escalation warning→critical is the key signal |
| `description` | yes | often carries the current metric readout |
| `labels` | yes | small, meaningful |
| `annotations` | yes | summary / runbook / value changes |
| `raw_payload` | no | large and churns every scrape (timestamps / trace ids) → noise |
| `name` | no | fingerprint-stable; effectively never changes |

### Implementation shape

- Add a small helper `_diff_alert(alert, parsed) -> dict` that returns
  `{field: [old, new], ...}` for the diffed fields where values differ.
- Call it at the **top** of `_update_alert`, before the assignments overwrite the old
  values.
- Status-unchanged branch: always `AlertHistory.objects.create(..., event="updated",
  old_status=alert.status, new_status=alert.status, details={"changed": diff})` and keep
  `result.alerts_updated += 1`.
- Status-change branch: keep existing `old_status`/`new_status`/event semantics; also
  attach `details={"changed": diff}` for consistency.

## Tests

- Re-fire, no changes → one `"updated"` row, `details={"changed": {}}`.
- Re-fire, severity warning→critical → `"updated"` row,
  `details={"changed": {"severity": ["warning", "critical"]}}`.
- Re-fire, annotation change → diff captured in `details`.
- Re-fire N times → N `"updated"` rows (regression proof for the original symptom).
- Existing status-change tests still pass; resolved/refired rows now also carry a diff.

## Out of scope (flagged, not solved)

Full fidelity means a checker firing every scrape interval for days produces one row per
scrape — `AlertHistory` grows unbounded for noisy alerts. This tradeoff is accepted. No
retention / pruning is added here (YAGNI); a retention story is a candidate future
follow-up if volume becomes a problem.
