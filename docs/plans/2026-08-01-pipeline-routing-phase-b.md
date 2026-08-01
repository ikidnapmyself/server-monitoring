---
title: "Phase B: Pipeline Shape from Flags — Implementation Plan"
parent: Plans
---

# Phase B: Pipeline Shape from `Pipeline` Flags

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the pipeline's *shape* come from the matched `PipelineDefinition` — resolve the pipeline right after `INGEST`, stamp it on the `Incident`, and select the remaining stages from its `run_checkers`/`run_intelligence`/`run_notify` flags. Along the way, give every `Alert` a `trace_id` (so the journey chain is complete) and a `node` link (so a hub can query results per agent).

**Architecture:** The engine (executors, run loop, retries, signals, `PipelineRun`/`StageExecution` audit) is preserved. The one real edit is *when* stage selection happens: today it is chosen up front; Phase B makes `INGEST` always run, then resolves the `Pipeline`, then selects the downstream stages from its flags. `checks_only`/`skip_checkers` payload flags survive as **CLI overrides** (non-breaking); a no-match falls back to today's full `STAGE_ORDER`. The `Incident` stamp moves from notify-time (Phase A) to right-after-ingest, so `NotifyExecutor` just *reads* `incident.pipeline`.

**Design:** `docs/plans/2026-08-01-pipeline-routing-north-star-design.md` (Phase B bullet) and Phase A (`docs/plans/2026-08-01-pipeline-routing-phase-a.md`), which already added the routing fields, `matches()`, `resolve_pipeline()`, `facts_from_incident()`, and `Incident.pipeline`.

**Tech Stack:** Django 5.2, pytest, uv. **Conventions:** absolute imports; line length 100; 100% branch coverage on changed lines; TDD; one commit per task; never push to `main` (feature branch + PR).

**Scope decisions (do NOT relitigate):**
- `Incident.pipeline` FK already exists (Phase A, Task 3) — Phase B only moves *when* it is stamped (after ingest instead of at notify).
- **No `Alert.pipeline` FK.** It is derivable via `alert.incident.pipeline`; adding a second denormalized FK is YAGNI. The design's "Alert/Incident FK → Pipeline" is satisfied by `Incident.pipeline` + `Alert.node`.
- `skip_checkers`/`checks_only` are **kept working** as CLI/back-compat overrides (the design's "retire … keep it working during migration"). This plan does *not* delete them; the inbox/no-match path is Phase C.
- **Branch name:** `feat/pipeline-routing-phase-b`, cut from up-to-date `main` **after PR #183 (Phase A) merges**.

---

## Task 0: Branch setup

**Step 1:** Confirm Phase A (PR #183) is merged, then:

```bash
git checkout main && git pull
git checkout -b feat/pipeline-routing-phase-b
```

Expected: on a fresh branch off main; `apps/orchestration/routing.py` and `Incident.pipeline` present (Phase A landed).

---

## Task 1: `Alert.trace_id` — every alert carries its run's correlation ID

**Why:** The journey (`Alert → Incident → PipelineRun → StageExecution`) is a projection joined by `trace_id`. Today `Alert` has no `trace_id`, so a checker- or webhook-originated alert can't be walked back to its run. `CheckRun.trace_id` already exists but is not even populated for pipeline runs (the bridge isn't given the id).

**Files:**
- Modify: `apps/alerts/models.py` (add field to `Alert`)
- Create: `apps/alerts/migrations/0005_alert_trace_id.py` (via `makemigrations`)
- Modify: `apps/alerts/services.py:179` (`AlertOrchestrator._create_alert` — accept + stamp `trace_id`)
- Modify: `apps/alerts/check_integration.py` (`CheckAlertBridge.__init__` accept `trace_id`; `_create_alert` stamp it; forward to `checker.run(trace_id=...)`)
- Modify: `apps/orchestration/executors.py:56` (`IngestExecutor.execute` — pass `ctx.trace_id` into the ingest path) and `:138` (`CheckExecutor.execute` — pass `ctx.trace_id` into `CheckAlertBridge`)
- Test: `apps/alerts/_tests/test_check_integration.py`, `apps/orchestration/_tests/test_executors.py`

**Step 1: Write the failing model/field test**

Add to `apps/alerts/_tests/` (e.g. `test_models.py` or the check-integration test):

```python
def test_check_created_alert_carries_trace_id(self):
    from apps.alerts.check_integration import CheckAlertBridge
    from apps.alerts.models import Alert

    bridge = CheckAlertBridge(trace_id="trace-abc")
    # drive one failing check so an alert is created
    bridge.run_checks_and_alert(checker_names=["cpu"], checker_configs={"cpu": {"critical_threshold": 0.0}})
    alert = Alert.objects.filter(source="checker").order_by("-received_at").first()
    assert alert is not None
    assert alert.trace_id == "trace-abc"
```

**Step 2: Run it, expect failure** — `pytest apps/alerts/_tests/... -v` → FAIL (`trace_id` attribute / kwarg missing).

**Step 3: Add the field**

In `apps/alerts/models.py`, on `Alert` (near the other correlation-ish fields):

```python
    trace_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Correlation ID of the pipeline run that produced this alert.",
    )
```

Run: `uv run python manage.py makemigrations alerts` → creates `0005_alert_trace_id.py`.

**Step 4: Thread the id through**

- `CheckAlertBridge.__init__(..., trace_id: str = "")` → store `self.trace_id = trace_id`.
- In `CheckAlertBridge._create_alert(...)`, add `trace_id=self.trace_id` to the `Alert.objects.create(...)` call.
- Where the bridge invokes `checker.run(...)` (in `run_check_and_alert` / `run_checks_and_alert`), pass `trace_id=self.trace_id` so `CheckRun.trace_id` is populated too.
- `apps/alerts/services.py` `AlertOrchestrator._create_alert(...)` → accept a `trace_id` and set it on `Alert.objects.create(...)`; thread it from the caller in `IngestExecutor`.
- `CheckExecutor.execute`: `bridge_kwargs["trace_id"] = ctx.trace_id` before constructing the bridge.
- `IngestExecutor.execute`: pass `ctx.trace_id` into the orchestrator ingest call so webhook alerts are stamped.

**Step 5: Add the webhook-side test**

```python
def test_ingested_alert_carries_trace_id(self):
    # run IngestExecutor with a sample payload and assert the created Alert.trace_id == ctx.trace_id
```

**Step 6: Run tests + coverage**

```bash
uv run pytest apps/alerts/_tests/ apps/orchestration/_tests/test_executors.py -q
uv run coverage run --branch -m pytest apps/alerts/_tests/ apps/orchestration/_tests/test_executors.py -q
uv run coverage report --include="*/check_integration.py,*/alerts/services.py,*/executors.py"
```

Expected: PASS; 100% on changed lines.

**Step 7: Commit**

```bash
git add -A && git commit -m "feat(alerts): stamp trace_id on every alert (checker + webhook paths)"
```

---

## Task 2: Resolve pipeline after `INGEST`; stamp `Incident`; select stages from flags

**Why:** This is the core of Phase B — the pipeline's shape becomes data. After `INGEST` yields source/severity/labels, resolve the matched `Pipeline`, stamp it on the `Incident` once, and run only the stages its flags enable.

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (`_run_pipeline_stages`, ~262–297 — restructure stage selection)
- Modify: `apps/orchestration/executors.py` (`NotifyExecutor._route_incident` — read `incident.pipeline` instead of re-resolving)
- Test: `apps/orchestration/_tests/test_orchestrator_routing.py` (create), `apps/orchestration/_tests/test_pipeline_routing.py` (extend)

**Selection precedence (implement exactly):**
1. `payload["checks_only"]` → `[CHECK]` (unchanged CLI override).
2. `payload["skip_checkers"]` → `STAGE_ORDER` minus `CHECK` (unchanged; back-compat, kept during migration).
3. Else: run `INGEST`; resolve `Pipeline` from the incident's facts; if a pipeline matched, downstream = `[CHECK if run_checkers] + [ANALYZE if run_intelligence] + [NOTIFY if run_notify]`.
4. Else (no pipeline matched): today's full `STAGE_ORDER` (non-breaking; inbox/no-match is Phase C).

**Step 1: Write the failing test**

`apps/orchestration/_tests/test_orchestrator_routing.py`:

```python
from django.test import TestCase


class StageSelectionFromFlagsTests(TestCase):
    def _run_sample(self):
        # run PipelineOrchestrator on a --sample-style payload; return the PipelineRun
        ...

    def test_run_checkers_false_skips_check_stage(self):
        from apps.orchestration.models import PipelineDefinition
        PipelineDefinition.objects.create(name="no-check", match=[], priority=1, run_checkers=False)
        run = self._run_sample()
        stages = list(run.stage_executions.values_list("stage", flat=True))
        assert "check" not in stages
        assert "notify" in stages

    def test_run_notify_false_stops_before_notify(self):
        from apps.orchestration.models import PipelineDefinition
        PipelineDefinition.objects.create(name="silent", match=[], priority=1, run_notify=False)
        run = self._run_sample()
        stages = list(run.stage_executions.values_list("stage", flat=True))
        assert "notify" not in stages

    def test_no_matching_pipeline_runs_full_order(self):
        run = self._run_sample()  # no pipelines defined
        stages = set(run.stage_executions.values_list("stage", flat=True))
        assert {"ingest", "check", "analyze", "notify"} <= stages

    def test_incident_stamped_after_ingest(self):
        from apps.orchestration.models import PipelineDefinition
        p = PipelineDefinition.objects.create(name="ca", match=[], priority=1)
        run = self._run_sample()
        run.refresh_from_db()
        from apps.alerts.models import Incident
        assert Incident.objects.get(id=run.incident_id).pipeline_id == p.id

    def test_skip_checkers_payload_override_still_works(self):
        # payload skip_checkers=True → no check stage even with run_checkers=True pipeline
        ...
```

**Step 2: Run, expect failure** — `pytest apps/orchestration/_tests/test_orchestrator_routing.py -v` → FAIL (CHECK still runs; incident stamped only at notify).

**Step 3: Restructure `_run_pipeline_stages`**

Replace the up-front `active_stages` block (~262–273) so `INGEST` is always first and the downstream stages are chosen *after* ingest. Sketch:

```python
checks_only = payload.get("checks_only", False)
skip_checkers = payload.get("skip_checkers", False)  # back-compat CLI override

if checks_only:
    active_stages = [PipelineStage.CHECK]
    final_status = PipelineStatus.CHECKED
else:
    # INGEST always runs; downstream stages are resolved from the matched Pipeline
    # after we know the incident's facts (see _downstream_stages below).
    active_stages = [PipelineStage.INGEST]
    final_status = PipelineStatus.NOTIFIED
```

After the `INGEST` result is recorded and `incident_id` is set (the block at ~313–325), insert:

```python
if stage == PipelineStage.INGEST and not checks_only:
    downstream = self._downstream_stages(incident_id, skip_checkers)
    active_stages = [PipelineStage.INGEST] + downstream
    final_status = downstream[-1_status...]  # NOTIFIED if notify ran, else CHECKED/ANALYZED
```

Because the loop iterates `active_stages`, extend it in place *after* `INGEST` executes. Add a helper:

```python
def _downstream_stages(self, incident_id, skip_checkers):
    """Stages after INGEST, from the matched Pipeline's flags (or today's default)."""
    from apps.alerts.models import Incident
    from apps.orchestration.routing import facts_from_incident, resolve_pipeline

    default = [PipelineStage.CHECK, PipelineStage.ANALYZE, PipelineStage.NOTIFY]
    if skip_checkers:
        default = [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
    incident = Incident.objects.filter(id=incident_id).first() if incident_id else None
    if incident is None:
        return default
    matched = resolve_pipeline(facts_from_incident(incident))
    if matched is None:
        return default
    if incident.pipeline_id != matched.id:
        incident.pipeline = matched
        incident.save(update_fields=["pipeline", "updated_at"])
    stages = []
    if matched.run_checkers and not skip_checkers:
        stages.append(PipelineStage.CHECK)
    if matched.run_intelligence:
        stages.append(PipelineStage.ANALYZE)
    if matched.run_notify:
        stages.append(PipelineStage.NOTIFY)
    return stages
```

> **Implementer note:** decide the cleanest way to extend the loop — either (a) compute `active_stages` fully after the `INGEST` iteration and let the `for` continue over the new tail (mutating the list being iterated is fragile), or (b) restructure to run `INGEST` explicitly, then loop over `[INGEST] + downstream` for the resume/`_stage_completed` logic. **Prefer (b):** build the complete `active_stages` list *before* the main loop by running a lightweight resolve, OR run INGEST, then compute `downstream`, then iterate. Keep `_stage_completed`/resume semantics intact and keep `final_status` correct (last stage that actually ran). Cover every branch.

**Step 4: Simplify `NotifyExecutor._route_incident`**

Now the incident is already stamped after ingest, so notify just reads it:

```python
def _route_incident(self, ctx: StageContext) -> str | None:
    """Return the matched pipeline's primary active channel (pipeline stamped after INGEST)."""
    if not ctx.incident_id:
        return None
    from apps.alerts.models import Incident

    incident = Incident.objects.filter(id=ctx.incident_id).first()
    if incident is None or incident.pipeline_id is None:
        return None
    channel = incident.pipeline.channels.filter(is_active=True).order_by("name").first()
    return channel.name if channel else None
```

Update `apps/orchestration/_tests/test_pipeline_routing.py::RouteIncidentTests` to pre-stamp `incident.pipeline` (matching the new contract) instead of expecting `_route_incident` to resolve. Keep 100% branch coverage.

**Step 5: Run tests + coverage**

```bash
uv run pytest apps/orchestration/_tests/ -q
uv run coverage run --branch -m pytest apps/orchestration/_tests/ -q
uv run coverage report --include="*/orchestrator.py,*/executors.py"
```

Expected: PASS; 100% on changed lines.

**Step 6: Commit**

```bash
git add -A && git commit -m "feat(orchestration): select stages from the matched pipeline's flags"
```

---

## Task 3: `Alert.node` — link results to the agent they came from

**Why:** A hub receives cluster pushes and upserts a `Node` per `instance_id` (existing behaviour). Linking each ingested `Alert` to its `Node` gives the hub a queryable "everything from web-03" spine (admin is an operations surface; this is the read-model prerequisite named for Phase D). **Minimal:** stamp only when an `instance_id` label resolves to an *existing* `Node` — do **not** create nodes here (`Node.upsert` on the push owns that).

**Files:**
- Modify: `apps/alerts/models.py` (add `node` FK to `Alert`)
- Create: `apps/alerts/migrations/0006_alert_node.py`
- Modify: the alert-creation paths (`services.py` for webhook, `check_integration.py` for checker) to set `node` when resolvable
- Modify: `apps/alerts/admin.py` (show `node` on the Alert changelist/detail)
- Test: `apps/alerts/_tests/`

**Step 1: Failing test**

```python
def test_ingested_alert_links_existing_node(self):
    from apps.alerts.models import Alert, Node
    node = Node.objects.create(instance_id="web-03", hostname="web-03")
    # ingest an alert whose labels include instance_id=web-03
    alert = ...  # create via the ingest path
    assert alert.node_id == node.id

def test_alert_without_instance_label_has_no_node(self):
    alert = ...  # labels without instance_id
    assert alert.node_id is None
```

**Step 2: Run, expect failure** (no `node` field).

**Step 3: Add the field**

```python
    node = models.ForeignKey(
        "alerts.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="alerts",
        help_text="Agent this alert came from (resolved from the instance_id label).",
    )
```

`uv run python manage.py makemigrations alerts` → `0006_alert_node.py`.

**Step 4: Stamp at creation**

Add a tiny helper (e.g. in `apps/alerts/services.py` or a small `node_link.py`):

```python
def resolve_node(labels: dict):
    from apps.alerts.models import Node
    instance_id = (labels or {}).get("instance_id")
    if not instance_id:
        return None
    return Node.objects.filter(instance_id=instance_id).first()
```

Call it in both `_create_alert` sites and set `node=...` on the create. **Reuse** the same `instance_id` label key that `facts_from_incident`/`Node.upsert` already use — do not invent a new one.

**Step 5: Admin**

In `apps/alerts/admin.py`, add `node` (or a `node_link`) to the Alert `list_display`/`readonly_fields` and, if useful, a `list_filter`. Match the existing admin style.

**Step 6: Run tests + coverage**, then commit:

```bash
uv run pytest apps/alerts/_tests/ -q
git add -A && git commit -m "feat(alerts): link alerts to their originating Node"
```

---

## Task 4: Verify, docs, finish branch

**Files:**
- Modify: `docs/Deployment.md` and/or `docs/Architecture.md` (flag-driven stage selection; trace_id/node on alerts)

**Step 1: Full gate**

```bash
uv run black . --check
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run   # no missing migrations
uv run pytest
uv run coverage run --branch -m pytest && uv run coverage report
```

Expected: black/ruff clean; no pending migrations; all tests pass; 100% branch coverage on changed lines.

**Step 2: Docs**

Add a short note (near the Phase A routing note in `docs/Deployment.md`) explaining that a matched pipeline's `run_checkers`/`run_intelligence`/`run_notify` flags now select which stages run (e.g. an AI-only or notify-only pipeline), that `checks_only`/`skip_checkers` remain CLI overrides, that a no-match still runs the full pipeline (inbox arrives in Phase C), and that every alert now carries `trace_id` + an optional `node` link.

**Step 3: Commit docs**

```bash
git add docs/ && git commit -m "docs: flag-driven stage selection + alert trace_id/node"
```

**Step 4: Finish the branch** (superpowers:finishing-a-development-branch)

```bash
git push -u origin feat/pipeline-routing-phase-b
gh pr create --base main --title "feat: pipeline shape from Pipeline flags (Phase B)" --body "<summary + test plan>"
```

---

## Acceptance criteria ("done")

1. A matched active `PipelineDefinition`'s `run_checkers`/`run_intelligence`/`run_notify` flags select which stages run after `INGEST`; the matched pipeline is stamped on the `Incident` immediately after ingest.
2. `checks_only`/`skip_checkers` payload flags still behave exactly as today (CLI back-compat); a no-match runs the full `STAGE_ORDER` (non-breaking).
3. `NotifyExecutor` sends to the already-stamped `incident.pipeline`'s primary active channel (fallback unchanged); it no longer re-resolves.
4. Every `Alert` created via the webhook or checker path carries the run's `trace_id`; `CheckRun.trace_id` is populated for pipeline runs.
5. Ingested alerts link to their `Node` when an `instance_id` label matches an existing node; alerts are unaffected otherwise.
6. All CI gates green: black, ruff, `makemigrations --check`, pytest, 100% branch coverage on changed lines; `pip-audit`/`bandit` unaffected.

## Out of scope (Phase C / D)

- Durable ingest, inbox, drain worker, no-match "collect and stay" (Phase C).
- Retiring `DefinitionBasedOrchestrator`, the journey admin panel + `manage.py trace` CLI, and the report read model over `Node`/`Pipeline`/incidents (Phase D).
- Deleting `skip_checkers`/`checks_only` (kept as overrides until a later cleanup).
- Any `Alert.pipeline` FK (derivable via `alert.incident.pipeline`).
