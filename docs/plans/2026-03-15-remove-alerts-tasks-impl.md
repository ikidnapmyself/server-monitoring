---
title: "Remove alerts/tasks.py Implementation Plan"
parent: Plans
---

# Remove `apps/alerts/tasks.py` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Delete the legacy Celery pipeline in `apps/alerts/tasks.py` and route the async webhook path through `apps.orchestration.tasks.run_pipeline_task`.

**Architecture:** The webhook view (`apps/alerts/views.py`) currently dispatches `orchestrate_event` from a local tasks module that reimplements the full 4-stage pipeline outside the orchestration app. We replace that single import/call with `run_pipeline_task` from `apps.orchestration.tasks`, then delete the legacy module and its tests.

**Tech Stack:** Django, Celery, pytest

---

### Task 1: Update `views.py` to use orchestration task

**Files:**
- Modify: `apps/alerts/views.py:54-71`

**Step 1: Update the Celery dispatch block**

Replace the import and call at lines 54-71. The old code dispatches `orchestrate_event.delay(ctx_dict)`. The new code dispatches `run_pipeline_task.delay(payload, source)`.

Change this block in `apps/alerts/views.py`:

```python
            if os.environ.get("ENABLE_CELERY_ORCHESTRATION", "1") == "1" and not celery_eager:
                try:
                    from apps.alerts.tasks import orchestrate_event

                    async_res = orchestrate_event.delay(
                        {
                            "trigger": "webhook",
                            "payload": payload,
                            "driver": driver,
                        }
                    )
                    return JsonResponse(
                        {
                            "status": "queued",
                            "pipeline_id": async_res.id,
                        },
                        status=202,
                    )
```

To:

```python
            if os.environ.get("ENABLE_CELERY_ORCHESTRATION", "1") == "1" and not celery_eager:
                try:
                    from apps.orchestration.tasks import run_pipeline_task

                    async_res = run_pipeline_task.delay(
                        payload=payload,
                        source=driver or "unknown",
                    )
                    return JsonResponse(
                        {
                            "status": "queued",
                            "pipeline_id": async_res.id,
                        },
                        status=202,
                    )
```

**Step 2: Run existing webhook tests to verify nothing broke**

Run: `uv run pytest apps/alerts/_tests/views/test_webhook.py -v`
Expected: All 5 tests PASS

**Step 3: Commit**

```bash
git add apps/alerts/views.py
git commit -m "refactor: route async webhook through orchestration pipeline task"
```

---

### Task 2: Delete legacy tasks module and its tests

**Files:**
- Delete: `apps/alerts/tasks.py`
- Delete: `apps/alerts/_tests/test_tasks.py`

**Step 1: Delete both files**

```bash
rm apps/alerts/tasks.py apps/alerts/_tests/test_tasks.py
```

**Step 2: Run all alerts tests to confirm no imports break**

Run: `uv run pytest apps/alerts/_tests/ -v`
Expected: All tests PASS. No `ImportError` referencing `apps.alerts.tasks`.

**Step 3: Run full test suite to confirm no other module imports the deleted file**

Run: `uv run pytest`
Expected: All tests PASS.

**Step 4: Commit**

```bash
git add -u apps/alerts/tasks.py apps/alerts/_tests/test_tasks.py
git commit -m "refactor: delete legacy alerts tasks module and tests"
```

---

### Task 3: Update Architecture docs

**Files:**
- Modify: `docs/Architecture.md:128-136`

**Step 1: Remove the legacy chain section**

Replace this block in `docs/Architecture.md` (lines 128-136):

```markdown
### Celery Tasks

**Alert processing chain** (`apps.alerts.tasks`):

```
orchestrate_event → alerts_ingest → run_diagnostics → analyze_incident → notify_channels
```

Each stage task (except `orchestrate_event`) has `max_retries=3`.

**Pipeline tasks** (`apps.orchestration.tasks`):
```

With:

```markdown
### Celery Tasks

**Pipeline tasks** (`apps.orchestration.tasks`):
```

This removes the now-deleted legacy chain documentation and keeps the `apps.orchestration.tasks` table that follows.

**Step 2: Commit**

```bash
git add docs/Architecture.md
git commit -m "docs: remove references to deleted alerts tasks module"
```

---

### Task 4: Final verification

**Step 1: Search for any remaining references to the deleted module**

```bash
grep -r "apps.alerts.tasks" --include="*.py" --include="*.md" .
```

Expected: No results (or only this plan file and the design doc).

**Step 2: Run full test suite with coverage**

```bash
uv run coverage run -m pytest && uv run coverage report --show-missing
```

Expected: All tests PASS. No coverage regressions.

**Step 3: Run pre-commit hooks**

```bash
uv run pre-commit run --all-files
```

Expected: All hooks PASS.