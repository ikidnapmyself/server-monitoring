---
title: "Phase D: Retire the Graph Engine + Journey/Report Views — Implementation Plan"
parent: Plans
---

# Phase D: Retire `DefinitionBasedOrchestrator` + Journey/Report Views

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close out the north-star. **Retire the legacy node/edge graph engine** (`DefinitionBasedOrchestrator`, the `nodes/` package, the `PipelineDefinition.config` graph, the definition HTTP endpoints and CLI flags) now that routing (Phase A) + flag-driven stages (Phase B) + durable ingest (Phase C) fully replace it. Add the **journey** projection (admin panel + `manage.py trace`) and a **`manage.py report`** read model over `Node`/`Pipeline`/`Incident`.

**Architecture (decisions locked in brainstorming — do NOT relitigate):**
- **Full delete + drop the `config` column.** `PipelineDefinition` **stays** — it is the routing model (`match`/`priority`/`run_*`/`channels` from Phase A). What's retired is its legacy `config` node/edge graph and everything that reads it. A destructive migration drops `config` + `version`-on-config logic; `get_nodes`/`get_defaults`/`get_entry_node` are removed.
- **Journey = projection, no new model.** An admin panel on `Alert`/`Incident` and `manage.py trace <alert-id | trace_id>` render the existing chain `Alert → Incident → PipelineRun → StageExecution` + the matched `Pipeline`.
- **Report = a CLI command, no endpoint.** `manage.py report [--json]` aggregates over `Node`/`Pipeline`/`Incident` + inbox depth (per-node incident counts, per-pipeline routing hits, recent activity). Consistent with `doctor`/`monitor_pipeline`. No new HTTP surface (an API can come later if a concrete consumer appears).

**What is verified unused (safe to retire):** the 8 nodes run `run_pipeline --checks-only --json` via cron — the **main** `PipelineOrchestrator` path, never `--definition`/`--config`. The definition endpoints and graph CLI have no production consumer.

**Retirement inventory (delete unless noted):**
- `apps/orchestration/definition_orchestrator.py` (the engine)
- `apps/orchestration/nodes/` (whole package: `base/context/ingest/intelligence/notify/transform`)
- `apps/orchestration/services.py` — `PipelineInspector` + `PipelineDetail` (graph inspection); keep any non-graph helpers
- `apps/orchestration/management/commands/show_pipeline.py` (“Display pipeline definitions” — graph) and `pipelines/*.json` samples
- `run_pipeline` — remove `--definition`/`--config`/`--payload` branches + the `DefinitionBasedOrchestrator` import; **keep the command** and `--sample`/`--checks-only`/`--dry-run`
- `views.py` — `PipelineDefinitionListView`, `PipelineDefinitionDetailView`, `PipelineDefinitionValidateView`, `PipelineDefinitionExecuteView` **and** `PipelineListView` (`pipelines/`, lists graph definitions via `get_nodes`); **keep** `PipelineView` (`pipeline/`, `pipeline/sync/`), `PipelineStatusView`, `PipelineResumeView`
- `urls.py` — the `definitions/*` routes + `pipelines/`; keep the run routes
- `PipelineDefinition.config` field + `get_nodes/get_defaults/get_entry_node`; admin `node_count`; the `config`-change branch in `PipelineDefinitionAdmin.save_model`
- All associated tests (`test_definition_orchestrator.py`, `nodes/` tests, definition tests in `test_views.py`/`test_services.py`, `show_pipeline` tests, `run_pipeline` definition tests)

**Design:** `docs/plans/2026-08-01-pipeline-routing-north-star-design.md` ("retire `DefinitionBasedOrchestrator`; journey; report read model"). Builds on A (#183), B (#184), C (#185) — all merged.

**Tech Stack:** Django 5.2, pytest, uv. **Conventions:** absolute imports; line length 100; 100% branch coverage on changed lines; TDD; one commit per task; never push to `main` (feature branch + PR).

---

## Task 0: Branch setup

```bash
git checkout main && git pull
git checkout -b feat/pipeline-routing-phase-d
```

Expected: fresh branch off main with A+B+C present (routing fields, `_downstream_stages`, `process_inbox`, no Celery).

---

## Task 1: Retire the definition HTTP endpoints + graph pipeline list

**Why:** Remove the legacy definition API first (top of the dependency chain), so later deletions don't break URL routing/tests.

**Files:** Modify `apps/orchestration/views.py`, `apps/orchestration/urls.py`; update `apps/orchestration/_tests/test_views.py`.

**Steps (TDD-in-reverse for deletion):**
1. Delete the four `PipelineDefinition*` views + `PipelineListView` from `views.py` and their imports (`DefinitionBasedOrchestrator`, `PipelineInspector`, `get_nodes` usages).
2. Delete the `definitions/*` and `pipelines/` routes from `urls.py`.
3. Delete the corresponding tests in `test_views.py` (definition list/detail/validate/execute, pipeline-list). Keep the trigger/status/resume/sync tests (incl. the Phase C `test_async_mode_records_pending_run`).
4. Run `uv run pytest apps/orchestration/_tests/test_views.py -q` → green; confirm no other test references the removed routes (`grep -rn "definitions/\|pipeline-list\|PipelineDefinitionListView"`).

**Commit:** `refactor(orchestration): remove legacy definition HTTP endpoints`.

---

## Task 2: Trim `run_pipeline` to the main orchestrator; drop graph CLI + samples

**Files:** Modify `apps/orchestration/management/commands/run_pipeline.py`; delete `apps/orchestration/management/commands/pipelines/` (JSON samples) and `show_pipeline.py`; update/trim their tests.

**Steps:**
1. In `run_pipeline.py`: remove `--definition`, `--config`, `--payload` arguments, the `DefinitionBasedOrchestrator` import, and the branch that runs a definition. Keep `--sample`, `--checks-only`, `--source`, `--dry-run`, and the hardcoded `PipelineOrchestrator` path. Update the module docstring/examples.
2. Delete `show_pipeline.py` (+ its tests) and the `pipelines/*.json` sample configs.
3. Update `run_pipeline` tests: drop definition/config cases; keep sample/checks-only/dry-run.
4. `grep -rn "show_pipeline\|--definition\|--config\|pipelines/.*\.json"` across `apps/`, `bin/`, `docs/` to catch stragglers (e.g. `bin/` aliases, docs command tables) — fix or note.
5. Run the checkers/orchestration suites.

**Commit:** `refactor(orchestration): run_pipeline drops --definition/--config; remove graph samples`.

---

## Task 3: Delete `DefinitionBasedOrchestrator`, the `nodes/` package, and the graph inspector

**Files:** Delete `apps/orchestration/definition_orchestrator.py`, `apps/orchestration/nodes/` (whole dir), and the `PipelineInspector`/`PipelineDetail` classes in `apps/orchestration/services.py` (keep any non-graph code there); delete `apps/orchestration/_tests/test_definition_orchestrator.py` and any `nodes/`/inspector tests.

**Steps:**
1. `git rm` the engine, the `nodes/` package, and their tests.
2. Remove `PipelineInspector`/`PipelineDetail` from `services.py` (and re-check the file still imports cleanly / has remaining used code).
3. `grep -rn "DefinitionBasedOrchestrator\|orchestration.nodes\|PipelineInspector\|PipelineDetail"` across the repo → zero non-deleted references.
4. `uv run python manage.py check` + orchestration suite green.

**Commit:** `refactor(orchestration): delete DefinitionBasedOrchestrator + node graph`.

---

## Task 4: Drop `PipelineDefinition.config` (+ graph methods, admin, migration)

**Why:** The graph column and its accessors are now dead. `PipelineDefinition` keeps only the routing fields.

**Files:** Modify `apps/orchestration/models.py`, `apps/orchestration/admin.py`; create `apps/orchestration/migrations/00XX_drop_pipelinedefinition_config.py`.

**Steps:**
1. Remove `config` field and `get_nodes`/`get_defaults`/`get_entry_node` from `PipelineDefinition`.
2. In `admin.py`: remove `node_count` display + the "Configuration (legacy graph)" fieldset; simplify `save_model` (drop the `config`-change/`version` bump branch — keep `created_by` default). Adjust `list_display` if it referenced `node_count`.
3. `uv run python manage.py makemigrations orchestration` → a migration that removes the `config` column.
4. Fix any test that constructed `PipelineDefinition(config=...)` (several create it with `config={}` — remove that kwarg).
5. Full orchestration + admin + checkers-preflight suites green (preflight `check_pipeline_state` reads `PipelineDefinition` — confirm it doesn't touch `config`).

**Commit:** `refactor(orchestration): drop legacy PipelineDefinition.config graph`.

> After Tasks 1–4, `grep -rn "\.config\b" apps/orchestration` should only match `NotificationChannel.config` / provider config — never `PipelineDefinition.config`.

---

## Task 5: Journey admin panel on `Alert` / `Incident`

**Why:** One-click lifecycle: given an alert/incident, show its run + stages + the matched pipeline.

**Files:** Modify `apps/alerts/admin.py` (Alert + Incident admin); test `apps/alerts/_tests/` (admin).

**Steps:**
1. **Write the failing test:** load the `Incident` change page as admin; assert the response contains the run's `trace_id`, each `StageExecution` stage/status, and the matched `pipeline` name (when set). Do the same shortcut on `Alert` (its `trace_id` links to the run).
2. Implement a read-only "Journey" section — a `readonly_fields` method (e.g. `journey_display(self, obj)`) returning safe HTML that walks `Incident → pipeline_runs → stage_executions` (ordered), showing stage, status, duration, output ref, and the matched `Pipeline` (`incident.pipeline`) with "why it routed" (the stamp). For `Alert`, link via `alert.trace_id` / `alert.incident`. Use `select_related`/`prefetch_related` to avoid N+1; escape all values.
3. Handle the **unhandled** case: an alert with no run (inbox) shows "not processed — inbox" rather than an empty panel.
4. Run alerts admin suite; 100% branch coverage on the new method.

**Commit:** `feat(alerts): journey panel on Alert/Incident admin`.

---

## Task 6: `manage.py trace <alert-id | trace_id>` CLI

**Why:** The CLI shortcut for the same journey — for terminals/CI.

**Files:** Create `apps/orchestration/management/commands/trace.py`; test `apps/orchestration/_tests/commands/test_trace.py`.

**Steps:**
1. **Failing tests:** given an alert id → prints its `trace_id`, incident, the `PipelineRun` status, each stage (stage/status), and "handled by pipeline #N (priority P)" or "inbox — not processed". Given a `trace_id` directly → same chain. Unknown id/trace → `CommandError`. `--json` emits the structured chain.
2. Implement: resolve the arg as an int alert id first, else treat as a `trace_id`; walk `Alert → Incident → PipelineRun(trace_id) → StageExecution[]`; render text + `--json`. Reuse the routing stamp (`incident.pipeline`) for the "handled by" line. No new model — pure query.
3. 100% branch coverage.

**Commit:** `feat(orchestration): manage.py trace <alert|trace_id> journey CLI`.

---

## Task 7: `manage.py report` read model (Node / Pipeline / Incident / inbox)

**Why:** The aggregate read model the north-star named — operational reporting without a new endpoint.

**Files:** Create `apps/orchestration/management/commands/report.py`; test `apps/orchestration/_tests/commands/test_report.py`.

**Steps:**
1. **Failing tests:** with a couple of `Node`s, `Incident`s (some linked to nodes via `Alert.node`, some open), and `PipelineDefinition`s (some stamped on incidents) → `report --json` returns:
   - `nodes`: per-node `{instance_id, incidents, open}` counts (via `Alert.node` → `Incident`),
   - `pipelines`: per-pipeline `{name, routed}` (count of `Incident.pipeline == p`),
   - `incidents`: totals by status,
   - `inbox`: `{pending, processing}` (reuse the `PipelineStatus` counts, same as `doctor`).
   Text mode prints the aligned summary from the design preview.
2. Implement with aggregate queries (`.values().annotate(Count)`), no per-row loops. Reuse `doctor`'s inbox counts (extract a shared helper or duplicate the two counts — keep it simple).
3. 100% branch coverage.

**Commit:** `feat(orchestration): manage.py report — node/pipeline/incident read model`.

---

## Task 8: Verify + docs + finish branch

**Files:** `docs/Architecture.md` (remove the "Definition-Based Pipeline" section + the dual-engine comparison table; the flat `Pipeline` is the only model now), `docs/Index.md` (drop definition endpoints/`show_pipeline`), `apps/orchestration/AGENTS.md` (remove node-graph/`validate_config`/`_NODE_HANDLERS` invariants; add journey/trace/report), any `bin/`/README command tables referencing the removed CLI.

**Steps:**
1. Full gate:
   ```bash
   uv run black . --check && uv run ruff check . && uv run mypy .
   uv run python manage.py makemigrations --check --dry-run
   uv run pytest && uv run coverage run --branch -m pytest && uv run coverage report
   uv run pip-audit --strict --desc
   ./bin/tests/test_helper/bats-core/bin/bats bin/tests/
   ```
   Expected: all clean; 100% branch coverage on changed lines; no pending migrations.
2. Docs updated; `grep -rn "DefinitionBasedOrchestrator\|definition-based\|show_pipeline\|--definition"` across `docs/`, `bin/`, `README.md` → only historical `docs/plans/` remain.
3. **Finish** (superpowers:finishing-a-development-branch) — push, open PR to `main`.

---

## Acceptance criteria ("done")

1. `DefinitionBasedOrchestrator`, the `nodes/` package, the graph inspector, the definition HTTP endpoints, `show_pipeline`, the `run_pipeline --definition/--config` flags, and the sample JSON pipelines are gone; `run_pipeline --sample/--checks-only/--dry-run` and the main orchestrator path are unchanged.
2. `PipelineDefinition.config` (+ `get_nodes/get_defaults/get_entry_node`) is dropped via migration; the model keeps only routing fields; nothing reads `PipelineDefinition.config`.
3. The `Alert`/`Incident` admin shows a read-only Journey panel (run + stages + matched pipeline; "inbox — not processed" when unhandled).
4. `manage.py trace <alert-id | trace_id>` renders the same chain (text + `--json`); unknown input errors cleanly.
5. `manage.py report [--json]` reports per-node incident counts, per-pipeline routing hits, incident totals, and inbox depth.
6. All CI gates green: black, ruff, mypy, `makemigrations --check`, pytest, 100% branch coverage on changed lines, `pip-audit --strict`, bats.

## Out of scope (explicit)

- A report **HTTP API** — deferred until a concrete consumer exists (the CLI is the read model for now).
- Any change to the routing semantics, durable ingest, or drain (A/B/C are done).
- Re-homing sample pipelines elsewhere — the hardcoded `--sample` payload remains the demo path.
- Touching `docs/plans/` historical records.
