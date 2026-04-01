---
title: "Remove apps/alerts/tasks.py — Route Async to Orchestration App"
parent: Plans
nav_order: 79739684
---

# Remove `apps/alerts/tasks.py`

## Problem

`apps/alerts/tasks.py` implements a legacy Celery-based pipeline that duplicates what `apps/orchestration/` does properly. It violates the project's boundary rule ("Only `apps.orchestration` advances the pipeline") by running all 4 stages from inside `apps.alerts`, bypassing `PipelineOrchestrator` entirely.

Consequences of the current state:
- No `PipelineRun` / `StageExecution` records — runs through this path are invisible
- No `trace_id` / `run_id` correlation
- Retry logic reimplemented ad-hoc via Celery decorators instead of the orchestrator's state machine
- `notify_channels` task hardcodes its own driver lookup, severity mapping, and message formatting

Current test coverage: 12%.

## Decision

Delete `apps/alerts/tasks.py` and route the async webhook path through `apps.orchestration.tasks.run_pipeline_task`.

## Changes

1. **`apps/alerts/views.py`** — Replace `apps.alerts.tasks.orchestrate_event` import with `apps.orchestration.tasks.run_pipeline_task`. Adapt `.delay()` call to match `run_pipeline_task`'s signature (`payload`, `source`, etc.). Keep the 202 async response shape.

2. **`apps/alerts/tasks.py`** — Delete entirely.

3. **`apps/alerts/_tests/test_tasks.py`** — Delete entirely.

4. **`apps/alerts/_tests/views/test_webhook.py`** — Update mocks referencing `apps.alerts.tasks.orchestrate_event` to reference `apps.orchestration.tasks.run_pipeline_task`.

5. **`docs/Architecture.md`** — Update references to old task names if they point to `apps.alerts.tasks`.

## What stays the same

- Async/sync split in `views.py` (Celery available → 202; Celery down → sync fallback)
- `ENABLE_CELERY_ORCHESTRATION` env var
- `CELERY_TASK_ALWAYS_EAGER` check
- Sync fallback calls `AlertOrchestrator.process_webhook()` directly