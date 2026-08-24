---
title: "Incident Lifecycle Through the Inbox"
parent: Plans
---

# Incident Lifecycle Through the Inbox

**Date:** 2026-08-24
**Prior art:** `2026-08-19-incident-fanout-design.md` (the incident-event unit of work and the
materiality gate), `2026-08-21-alert-write-path-unification-design.md` (one alert write path),
`2026-05-31-observability-overbuild-postmortem.md` (why this adds no new mechanism).

## Problem

Three legs each hold their own truth and nothing reconciles them:

- **Alert** — the node's fact. The only input to the materiality gate
  (`apps/alerts/materiality.py`).
- **Incident** — the operator's case. Written by ingest (create / reopen / auto-resolve) and by
  admin actions; read by nothing downstream.
- **Orchestration** — one downstream run per materially-changed *alert's* incident
  (`orchestrator._enqueue_downstream_runs`), carrying the ingest snapshot into notify.

Operators do not watch the admin; they watch notifications. So the divergences that matter are
the ones that reach the pager:

1. Operator resolves / acknowledges / closes in admin → **nothing is sent**. On-call's last
   message still says "open".
2. Severity flips on a RESOLVED incident → a run is enqueued (materiality is symmetric) and
   notify says "resolved" around a critical alert. Only the resolved→firing refire branch in
   `AlertOrchestrator._update_alert` reopens; a severity change does not.
3. Acknowledge has no downstream effect at all — it is a label.
4. Notify renders from the INGEST `output_snapshot`, so it reports what the incident was when
   ingested, not what it is when the message goes out.

"Resolve as a snooze" therefore only works for the case that was already silent (same-severity
repeats) and fails for the two events a maintenance actually produces (severity flip, refire).

## Principle

**One event, one queue, one executor.** The event is *"incident N materially changed"*. The
queue is the inbox (PENDING `PipelineRun`). The executor is `process_inbox`. Nothing else runs a
pipeline; nothing else notifies.

Today that event has one producer — the alert write path. This design adds a second producer
(operator actions) and one consumer-side input (incident status). It adds **no** new origin, run
type, payload shape, endpoint, signal, or executor.

## Design

### 1. Operator actions are a second producer

Admin actions (`IncidentAdmin.resolve_selected`, `acknowledge_selected`, the object actions
`acknowledge_incident` / `resolve_incident` / `close_incident`) and their service twins in
`apps/alerts/services.py` keep doing exactly what they do: transition the incident synchronously.
The row reads RESOLVED the moment the request returns — no friction for the operator.

Then, and only then, they enqueue the same run the fan-out enqueues:
`_enqueue_downstream_runs(parent=None, [incident.id])` with `origin=PipelineOrigin.MANUAL`
(already exists, already means "a human did this") and a fresh `trace_id`. Payload is the existing
`{"downstream_incident_id": N}` — nothing else. Who did it lives where it lives today
(`AlertHistory` / `incident.metadata["acknowledged_by"]`), not in the run.

The notification about the transition therefore arrives with the same lag as everything else:
the next drain. This is accepted: if the drain interval is fine for a firing CRITICAL it is fine for
its resolution, and the knob for both is `process_inbox --loop` / the cron interval — global, not
per-event. **In-request execution is explicitly rejected**; it would be a second executor.

`_enqueue_downstream_runs` moves out of the orchestrator's private surface into a small public
helper (e.g. `apps.orchestration.inbox.enqueue_incident_runs(incident_ids, *, origin, trace_id)`),
so the alert path and the admin path call one function. Two callers, one implementation.

### 2. Incident status is an input to the gate

Where the alert write path decides an alert change is material, the *incident* decides whether
that produces a run. One function, `apps/alerts/gate.py` (name open), consulted by both alert
write paths before an incident id lands in `material_incident_ids`:

| Incident status | Alert change | Result |
|---|---|---|
| OPEN | any material | enqueue (today) |
| ACKNOWLEDGED | severity **rose** | reopen (ACK → OPEN), enqueue — escalation breaks an ack |
| ACKNOWLEDGED | refire / same or lower severity | absorb: history row only, no run |
| RESOLVED / CLOSED | alert firing (refire **or** severity change) | reopen, enqueue — closes gap 2 |
| RESOLVED / CLOSED | alert resolved | absorb (already the case) |

The reopen-on-refire logic that lives inline in `_update_alert` moves into this function so there
is one place that answers "does the incident follow the alert, and does anyone hear about it".
A future snooze (`snoozed_until`) is one more row in this table and nothing else — deferred.

### 3. Notify reads live incident state

`apps/orchestration/formatters.py` builds title / severity / lead from the INGEST snapshot. For
downstream runs it reads the `Incident` row at format time instead: status, current severity of
the subject alert, title. The ingest snapshot stays for what it is good at (counts, what this push
did). A message about a RESOLVED incident says resolved; a message about an OPEN one says open,
regardless of what was true a drain ago.

### 4. What operators see

- Admin incident change view already links to runs; a resolve now shows its PENDING run
  immediately, so "did on-call hear?" is answered by the run's status, not by guessing.
- `manage.py trace <trace_id>` shows the operator run like any other.
- Lanes already match on `status` / `origin`; "resolved → notify only, skip analyze" is
  configuration, not code.

## Out of scope

Snooze / maintenance windows; ack timeouts; per-channel behaviour on resolve; any change to
webhook or checker ingestion; any new management command.

## Inventory (scope discipline)

| Need | Existing capability | Reused as |
|---|---|---|
| Durable, retryable, single executor | inbox (`apps/orchestration/inbox.py`) | unchanged |
| "One run per changed incident" | `_enqueue_downstream_runs` | promoted to a shared helper |
| "A human did this" | `PipelineOrigin.MANUAL` | the origin for operator runs |
| Route by incident state | lane `match` on `status` / `origin` | unchanged |
| Audit of who acted | `AlertHistory`, `incident.metadata` | unchanged |

Nothing new is built that the table does not already name.

## Testing

- Gate table above, one test per row, for **both** alert write paths (`AlertOrchestrator`,
  `CheckAlertBridge`) — the duplication hazard named in the fan-out plan.
- Admin action → exactly one PENDING run with `origin=manual` and `downstream_incident_id`;
  no stage executed inside the request.
- Drain of that run → notify message reflects the incident's live status.
- Severity change on a RESOLVED incident → incident OPEN, one run, message says open.
- Refire on an ACKNOWLEDGED incident at equal severity → no run, history row present.
- 100% branch coverage on changed code.

## Acceptance

Every notification an operator receives has a `PipelineRun` in the inbox with its `trace_id`, and
every incident transition — from a node or from a human — is visible as one such run. There is
one queue to watch and one log to read.
