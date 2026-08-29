---
title: "Incidents as the Pipeline Subject"
parent: Plans
---

# Incidents as the Pipeline Subject

**Status:** approved design, 2026-08-29. Implementation plan to follow.

## The decision

Orchestration handles incidents and nothing else. Producing an alert is not a stage. Draining
synchronously is a mode of the one path, not a second path.

```
produce truth  →  roll into incidents  →  enqueue one run per changed incident  →  drain
```

Four producers feed the first step: an external webhook, a node push, this machine's checkers,
and an operator transition through `IncidentManager`. None of them orchestrates. Each writes
alerts, lets incidents form, and enqueues runs.

The drain is where synchronous and queued differ, and that is the only place they differ. Drain
in the same call and the operator gets a real-time checkup. Leave the runs and `process_inbox`
takes them on its own schedule. Same rows, same lanes, same executors, same outcome.

## Why

Three forces, all of which the current shape fights.

**A run can be three different things.** `_execute_pipeline` branches on whether the payload
carries `downstream_incident_id`, or `checks_only`, or neither, and each branch picks a different
entry stage. The entry stage is then the only stage allowed to route, which needs `run_routes`,
which needs its own carve-out for `--no-incidents`. That block is the most complicated code in
`apps/orchestration/orchestrator.py`, and every line of it exists because the subject of a run is
not fixed.

**CHECK has no subject.** A downstream run's payload is `{"downstream_incident_id": N}`. If its
lane lists `check`, `CheckExecutor` reads `checker_names` as `None` and sweeps the entire hub
registry. The seeded catch-all lane does list `check`. So an incident from an unmatched source
triggers a full hub checker run, producing alerts about the hub, on every material change, with
no relation to the incident that caused it.

**Checker truth and webhook truth are already the same thing.** PR #216 gave them one identity,
`check:{instance_id}:{checker_name}` under `source: cluster`, one registry, and one set of lanes.
What remains is that they still enter orchestration by different doors.

## What changes

### INGEST stops being a stage

Ingest becomes what happens before there is anything to orchestrate. The webhook parses, writes
alerts, lets incidents form, enqueues one run per materially changed incident, and returns 202.

This does not violate the rule the webhook view actually states. That rule is "no inline
pipeline", and what moves onto the request thread is bounded alert writes, not checkers,
inference, or delivery. Concurrency is already capped by worker count.

### Every run has an incident

`PipelineRun.incident` becomes non-null. `downstream_incident_id` disappears from payloads.
Routing always resolves from the incident's subject alert, which is already how child runs work.

Deleted along with the entry-stage concept: `run_routes`, the three-way branch, the
legacy-snapshot compatibility fallback, and the rule that only the entry stage may route.

Stages become diagnose, analyze, notify.

### Synchronous is a mode

The enqueue step takes a flag saying whether the caller drains its own runs before returning.
`run_pipeline` grew bespoke self-draining logic for this; that logic becomes the shared mode.

### CHECK gets a subject

A lane already decides whether `check` runs for the traffic it matches. `cluster-nodes` omits it,
with the reason in its own description: the node already ran its own checkers, so hub-side checks
would report the hub's CPU and disk. The catch-all includes it, which is right for a Grafana alert
about a room sensor, where nothing has been diagnosed yet.

What a lane runs is configuration and belongs to the operator, not to this design. What this design
fixes is that CHECK must not sweep the registry when the lane gave it no scope.

## What each command becomes

| Command | Role |
|---|---|
| `check_health` | This machine's checkers, then drains its own incidents. Real-time checkup and analysis, no daemon. |
| `push_to_hub` | A node reporting to its hub. Transport only. |
| `run_pipeline` | Replaying a webhook-shaped payload (`--sample`, `--payload`, `--file`). |
| `process_inbox` | The queued drain. |

`run_pipeline --checks-only` and `push_to_hub --local` become "check_health, draining" and
"check_health, not draining". Both are deprecated as pointers, not deleted.

Local checkers become identical to an agent's: same ingest, same identity, same incident rules.
The only difference is that an agent's results cross a network first.

## What must be preserved

Non-negotiable, since the whole goal is that nothing regresses.

- **Hub with agents.** Nightly pushes, queued drain, per-node config and re-evaluation.
- **Solo node.** `check_health` works with no hub, no cron, and no daemon. It now also analyses,
  which is the point.
- **Durable ingest under flood.** A burst must not run the pipeline inline.
- **Operator transitions.** Ack, resolve and close still announce through the same queue.
- **`--no-notify`.** Looking at a machine without paging anyone.

## Open points, and how they resolve

**The head of a trace.** `manage.py trace` resolves from `Alert.trace_id`, or from runs sharing a
`trace_id`, and falls back to the incident's runs. Alerts are stamped with `trace_id` at creation
and incident runs carry the same one, so removing the ingest run moves the head of the trace from
a run row to the alert. No new model is needed. What is lost is the stored raw payload on a run;
`Alert.raw_payload` already holds it per alert.

**Payload size.** Ingest on a web worker needs a cap on alerts per payload, rejected at the
webhook with a 4xx rather than accepted and half-written.

**`INGEST` history.** Existing `StageExecution` rows must keep rendering. `diagnosis.py`'s
`_STAGE_ORDER` and `_is_expected` treat `INGEST` as always expected; that becomes "expected for
runs recorded before this change". The stage value stays in the enum for history, and stops being
produced.

## Risks

The migration is behavioural, not schemaless. Runs in flight at deploy time were recorded under
the old model, and a resume must not silently drop their downstream work. The existing
legacy-snapshot fallback is precedent for how that was handled last time.

Making `PipelineRun.incident` non-null requires deciding what happens to historical rows that have
no incident. Backfilling from the run's subject alert is possible; leaving the column nullable at
the database level while treating it as required in code is the cheaper option and should be
considered first.
