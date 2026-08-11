---
title: "Incident stage diagnosis: expected-vs-actual debug strip"
parent: Plans
---

# Incident stage diagnosis: expected-vs-actual debug strip

## Problem

The system's value is trace-centric — given an incident you should see, at a glance,
where its pipeline flow broke or what silently never happened. Today that is hard: the
admin is model-at-a-time, and even the existing per-incident views only show **what
happened**, never the **negative space** (what should have happened but didn't). An
operator staring at incident 34 cannot quickly tell "notify never ran" from "notify ran
but sent nothing" from "notify is off by config".

## What already exists (inventory before building)

Per the repo's over-build post-mortem, we reuse rather than reinvent:

- `apps/alerts/timeline.py::build_incident_timeline` — merged chronological list of alert
  history + pipeline runs + stage executions. Rendered on the incident admin page as
  `journey_timeline`. Shows what happened, in order.
- `apps/alerts/admin.py::IncidentAdmin.journey_display` — per-run pipeline tree (routed-by
  pipeline + each run's `StageExecution`s with status/duration/attempt). Shows what ran.
- `apps/orchestration/management/commands/trace.py` — CLI trace.
- `config/dashboard.py::build_readiness` — system readiness panel.

**None of these surface negative space.** Critically, `Orchestrator._downstream_stages`
(`apps/orchestration/orchestrator.py:498`) shows that stages disabled by the routed
`PipelineDefinition` flags (`run_checkers` / `run_intelligence` / `run_notify`) or
`skip_checkers` produce **no `StageExecution` at all** — they are not marked `SKIPPED`,
they are simply absent. So "no execution" is ambiguous: a real gap, or a deliberate
config choice. Any diagnosis must consult the pipeline flags to tell them apart.

## Decision

Add **one pure classifier function** plus a **compact stage strip rendered in the existing
incident admin page**. No new app, URL, view, auth, or model. The classifier is the asset;
the render location is the cheapest one that already exists.

Constraint driving the whole design: **do not increase code complexity / surface.** This
stays a focused debug panel, not a second observability tool.

## Scope

- **Unit:** the incident (aggregates its many pipeline runs).
- **Signals surfaced, per stage:** ran-ok · ran-but-empty · failed/stalled · never-ran ·
  skipped-and-why (the full taxonomy the user asked for).
- **First slice:** single incident only. The whole-system "is everything flowing" view is
  a later phase built on the same classifier — explicitly **out of scope here**.

## Design

### 1. Pure classifier — `apps/alerts/diagnosis.py`

Mirrors `timeline.py`: read-only, no side effects, returns plain dicts. Placed in
`apps/alerts` (parallel to `timeline.py`); safe to import `apps.orchestration.models` there
(orchestration.models does not import alerts, so no cycle).

```python
def diagnose_incident(incident) -> list[dict]:
    """Return one diagnosis entry per expected pipeline stage for `incident`."""
```

Each entry:

- `stage`: one of `ingest | check | analyze | notify` (display maps to
  alerts/checkers/intelligence/notify).
- `status`: one of `ok | empty | failed | stalled | skipped | never_ran`.
- `detail`: short human string — skip reason, error message + retryable flag, or None.
- `runs`: `"succeeded in N/M runs"` rollup across the incident's runs.

**Expected stages.** `ingest` is always expected. For `check`/`analyze`/`notify`, consult
`incident.pipeline` flags (`run_checkers` / `run_intelligence` / `run_notify`). When
`incident.pipeline` is None (un-routed fallback), expected = all four (matches the
orchestrator's default full order). A stage that is *not* expected by flags is reported
`skipped` with `detail="config: <flag> disabled"` — never `never_ran`.

**Per-stage status (aggregated, incident-as-unit).** Look at the incident's runs newest-
first. The primary `status` comes from the **latest run that has an execution for that
stage**; `runs` is the succeeded-count rollup across all runs. Rules:

| Condition (on the latest relevant execution) | status |
|---|---|
| stage disabled by pipeline flags (no execution expected) | `skipped` (config reason) |
| explicit `StageExecution.status == skipped` | `skipped` (reason from `error_message`, strip `"Skipped: "`) |
| expected + enabled but **zero** executions in any run | `never_ran` |
| latest execution `status == failed` | `failed` (+ `error_type`/`error_message`, `error_retryable`) |
| latest execution `status in {running, retrying, pending}` on an unresolved run | `stalled` |
| latest execution `status == succeeded` but empty output (see below) | `empty` |
| latest execution `status == succeeded` with output | `ok` |

**Emptiness signal (low-complexity).** A stage is `empty` when it succeeded yet produced no
output we can see: `StageExecution.output_snapshot` is falsy **and** `output_ref == ""`
**and** the run's corresponding `PipelineRun.<stage>_output_ref` (`checker_output_ref` /
`intelligence_output_ref` / `notify_output_ref`) is empty. We deliberately do **not** query
`CheckRun` counts or notify message counts here — that cross-app enrichment can be layered
on later without changing the entry shape. (Documented as a future option, YAGNI for now.)

### 2. Render — `IncidentAdmin.diagnosis_display`

A new `@admin.display` readonly method on `IncidentAdmin`, added to `readonly_fields` and
placed in the Journey fieldset directly above `journey_display`. Renders the classifier
output as a compact strip using `format_html` / `format_html_join` (every dynamic value
escaped — same discipline as `journey_timeline`, since details derive from external
payloads). Colour/glyph per status, e.g.:

```
alerts    ✓ ok
checkers  ✓ ran → empty (0 output)        succeeded in 3/12 runs
intel     ⊘ skipped: config disabled
notify    ✗ never ran                      succeeded in 0/12 runs
```

No template file needed — it reuses the admin change page already on screen, reached from
alert 34 via `Alert.incident` in one click.

### 3. What this deliberately does NOT add

- No standalone page / new URL / new app / new auth.
- No CLI command (the pure function makes `manage.py diagnose_incident` a ~10-line wrapper
  later if wanted — not now).
- No per-run matrix (strip shows latest-run status + N/M rollup).
- No whole-system view (later phase, same classifier).
- No `CheckRun`/message-count queries (future enrichment).

## Testing

- **Pure-function unit tests** (`apps/alerts/_tests/test_diagnosis.py`) — one per taxonomy
  branch: ok, empty, failed (retryable + non-retryable), stalled, never_ran, skipped-by-
  explicit-execution, skipped-by-config-flag, and the un-routed (no pipeline) fallback. Plus
  the multi-run aggregation (`succeeded in N/M`, latest-run-wins).
- **Admin render smoke test** — `diagnosis_display` returns escaped HTML for a populated
  incident and a safe placeholder for an incident with no runs.
- 100% branch coverage on `diagnosis.py` per repo standard.

## Acceptance criteria

- Opening an incident shows a per-stage strip distinguishing all five states.
- A config-disabled stage reads `skipped: config …`, never `never_ran`.
- A stage with executions on some runs but not the latest, or never, is aggregated
  correctly with an `N/M` rollup.
- No new URL/view/app/model; only `diagnosis.py` + one admin method + tests.
