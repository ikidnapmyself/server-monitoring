# Incident Stage Diagnosis Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the incident admin page an expected-vs-actual stage strip that surfaces where a pipeline flow broke or silently never happened (ran-ok / ran-empty / failed / stalled / never-ran / skipped-and-why).

**Architecture:** One pure, read-only classifier `diagnose_incident(incident)` in `apps/alerts/diagnosis.py` (mirrors `apps/alerts/timeline.py`), rendered by a new `IncidentAdmin.diagnosis_display` method in the existing incident admin page. No new app, URL, view, auth, or model.

**Tech Stack:** Django 5.2, pytest/pytest-django (`TestCase`), Django admin (`format_html`).

**Design doc:** `docs/plans/2026-08-12-incident-stage-diagnosis-design.md`

**Branch:** `feat/incident-stage-diagnosis` (already checked out; design doc already committed).

---

## Context the executor needs

- **Precedent to mirror:** `apps/alerts/timeline.py` is a pure read-only projection returning `list[dict]`, tested in `apps/alerts/_tests/test_timeline.py`, and rendered by `IncidentAdmin.journey_timeline` (`apps/alerts/admin.py:465`). Copy that shape.
- **Models & enums:**
  - `apps.orchestration.models.PipelineStage` — `INGEST="ingest"`, `CHECK="check"`, `ANALYZE="analyze"`, `NOTIFY="notify"`.
  - `apps.orchestration.models.StageStatus` — `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `RETRYING`, `SKIPPED` (values are the lowercase names).
  - `StageExecution` (`apps/orchestration/models.py:319`) fields used: `pipeline_run` (FK), `stage`, `status`, `attempt`, `output_ref`, `output_snapshot` (JSON), `error_type`, `error_message`, `error_retryable`, `created_at`. `mark_skipped(reason)` stores `error_message = f"Skipped: {reason}"`.
  - `PipelineRun` (`apps/orchestration/models.py:55`) fields used: `created_at`, `checker_output_ref`, `intelligence_output_ref`, `notify_output_ref`, reverse `stage_executions`. Incident reverse relation: `incident.pipeline_runs`.
  - `PipelineDefinition` flags (`apps/orchestration/models.py:540`): `run_checkers`, `run_intelligence`, `run_notify` (all default True). Reached via `incident.pipeline` (nullable FK; `incident.pipeline_id`).
- **Key correctness fact** (`apps/orchestration/orchestrator.py:498` `_downstream_stages`): stages disabled by the routed pipeline flags produce **no `StageExecution`** (they are absent, not `SKIPPED`). So the classifier must treat a flag-disabled stage as `skipped (config)`, and only report `never_ran` for a stage that IS expected by flags yet has zero executions. `INGEST` is always expected. When `incident.pipeline` is None, all four stages are expected (matches the orchestrator's un-routed fallback).
- **Known limitation (document, do not solve):** the per-run `skip_checkers` runtime override is not stored on the incident, so a run skipped that way while `pipeline.run_checkers=True` would read as `never_ran`. Acceptable for this slice.
- **Verify commands:** `uv run pytest apps/alerts/_tests/test_diagnosis.py -v`, `uv run pytest apps/alerts/`, `uv run black .`, `uv run ruff check .`.

---

## Task 1: Pure classifier — expected stages, `never_ran` vs `skipped (config)`

Build the skeleton and the two branches that depend on pipeline flags first (the correctness crux).

**Files:**
- Create: `apps/alerts/diagnosis.py`
- Create: `apps/alerts/_tests/test_diagnosis.py`

**Step 1: Write the failing tests**

Create `apps/alerts/_tests/test_diagnosis.py`:

```python
"""Tests for the per-incident stage diagnosis classifier."""

from django.test import TestCase

from apps.alerts.diagnosis import diagnose_incident
from apps.alerts.models import Incident
from apps.orchestration.models import PipelineDefinition


class DiagnoseIncidentExpectedStagesTests(TestCase):
    def _statuses(self, incident):
        return {e["stage"]: e["status"] for e in diagnose_incident(incident)}

    def test_returns_four_stages_in_order(self):
        incident = Incident.objects.create(title="Empty")
        stages = [e["stage"] for e in diagnose_incident(incident)]
        self.assertEqual(stages, ["ingest", "check", "analyze", "notify"])

    def test_no_runs_no_pipeline_all_never_ran(self):
        incident = Incident.objects.create(title="No runs")
        self.assertEqual(
            self._statuses(incident),
            {"ingest": "never_ran", "check": "never_ran",
             "analyze": "never_ran", "notify": "never_ran"},
        )

    def test_flag_disabled_stage_reads_skipped_config_not_never_ran(self):
        pipe = PipelineDefinition.objects.create(
            name="no-intel", run_checkers=True, run_intelligence=False, run_notify=True,
        )
        incident = Incident.objects.create(title="Routed", pipeline=pipe)
        entries = {e["stage"]: e for e in diagnose_incident(incident)}
        self.assertEqual(entries["analyze"]["status"], "skipped")
        self.assertIn("config", entries["analyze"]["detail"])
        # check + notify remain expected -> never_ran (no executions yet)
        self.assertEqual(entries["check"]["status"], "never_ran")
        self.assertEqual(entries["notify"]["status"], "never_ran")
```

**Step 2: Run to verify they fail**

Run: `uv run pytest apps/alerts/_tests/test_diagnosis.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.alerts.diagnosis'`.

**Step 3: Write the implementation**

Create `apps/alerts/diagnosis.py`:

```python
"""Per-incident stage diagnosis.

A pure, read-only classifier: for each expected pipeline stage of an incident it
reports whether the stage ran cleanly, ran but produced nothing, failed, stalled,
never ran, or was skipped (and why). Aggregates across the incident's pipeline
runs. No side effects — reads the ORM and returns plain dicts, mirroring
``apps.alerts.timeline``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.orchestration.models import PipelineStage, StageStatus

if TYPE_CHECKING:
    from apps.alerts.models import Incident

# Canonical stage order (display maps ingest->alerts, check->checkers, etc.).
_STAGE_ORDER = [
    PipelineStage.INGEST,
    PipelineStage.CHECK,
    PipelineStage.ANALYZE,
    PipelineStage.NOTIFY,
]

# stage -> (pipeline flag attr or None if always-expected,
#           PipelineRun output-ref attr or None)
_STAGE_META = {
    PipelineStage.INGEST: (None, None),
    PipelineStage.CHECK: ("run_checkers", "checker_output_ref"),
    PipelineStage.ANALYZE: ("run_intelligence", "intelligence_output_ref"),
    PipelineStage.NOTIFY: ("run_notify", "notify_output_ref"),
}

_IN_PROGRESS = {StageStatus.PENDING, StageStatus.RUNNING, StageStatus.RETRYING}


def _is_expected(incident, stage) -> bool:
    """Is this stage expected to run for the incident's routed pipeline?"""
    flag_attr, _ = _STAGE_META[stage]
    if flag_attr is None:
        return True  # ingest always expected
    if incident.pipeline_id is None:
        return True  # un-routed fallback: full pipeline
    return bool(getattr(incident.pipeline, flag_attr))


def diagnose_incident(incident) -> list[dict]:
    """Return one diagnosis entry per expected pipeline stage for ``incident``.

    Each entry: ``stage`` (str), ``status`` (ok|empty|failed|stalled|skipped|
    never_ran), ``detail`` (str|None), ``runs`` (str|None rollup).
    """
    runs = list(
        incident.pipeline_runs.order_by("-created_at").prefetch_related("stage_executions")
    )
    total = len(runs)

    entries: list[dict] = []
    for stage in _STAGE_ORDER:
        entries.append(_diagnose_stage(incident, stage, runs, total))
    return entries


def _diagnose_stage(incident, stage, runs, total) -> dict:
    entry = {"stage": stage.value, "status": "never_ran", "detail": None, "runs": None}

    if not _is_expected(incident, stage):
        flag_attr, _ = _STAGE_META[stage]
        entry["status"] = "skipped"
        entry["detail"] = f"config: {flag_attr} disabled"
        return entry

    # Latest execution for this stage: newest run first, highest attempt within.
    latest = None
    succeeded_runs = 0
    for run in runs:
        execs = [e for e in run.stage_executions.all() if e.stage == stage.value]
        if not execs:
            continue
        if any(e.status == StageStatus.SUCCEEDED for e in execs):
            succeeded_runs += 1
        if latest is None:
            latest = max(execs, key=lambda e: e.attempt)

    if total:
        entry["runs"] = f"succeeded in {succeeded_runs}/{total} runs"

    if latest is None:
        entry["status"] = "never_ran"
        return entry

    _classify_from_execution(entry, latest, stage)
    return entry


def _classify_from_execution(entry, exc, stage) -> None:
    """Fill entry['status'] / ['detail'] from the latest StageExecution."""
    if exc.status == StageStatus.SKIPPED:
        entry["status"] = "skipped"
        reason = exc.error_message.removeprefix("Skipped: ") or "no reason recorded"
        entry["detail"] = reason
    elif exc.status == StageStatus.FAILED:
        entry["status"] = "failed"
        entry["detail"] = (
            f"{exc.error_type or 'error'}: {exc.error_message} "
            f"(retryable={exc.error_retryable})"
        )
    elif exc.status in _IN_PROGRESS:
        entry["status"] = "stalled"
    elif exc.status == StageStatus.SUCCEEDED:
        entry["status"] = "empty" if _is_empty(exc, stage) else "ok"


def _is_empty(exc, stage) -> bool:
    """A succeeded stage with no visible output snapshot or refs."""
    _, run_ref_attr = _STAGE_META[stage]
    run_ref_empty = True
    if run_ref_attr is not None:
        run_ref_empty = getattr(exc.pipeline_run, run_ref_attr) == ""
    return (not exc.output_snapshot) and exc.output_ref == "" and run_ref_empty
```

**Step 4: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_diagnosis.py -v`
Expected: PASS (3 tests).

**Step 5: Commit**

```bash
git add apps/alerts/diagnosis.py apps/alerts/_tests/test_diagnosis.py
git commit -m "feat(alerts): add diagnose_incident classifier (expected-stage skeleton)"
```

---

## Task 2: Classify from executions — ok / empty / failed / stalled / skipped

Cover every status branch of `_classify_from_execution` and `_is_empty`. (Implementation already written in Task 1; this task proves it with tests. If a test reveals a bug, fix the classifier and note it.)

**Files:**
- Test: `apps/alerts/_tests/test_diagnosis.py`

**Step 1: Write the failing tests**

Add to `apps/alerts/_tests/test_diagnosis.py`:

```python
from apps.orchestration.models import PipelineRun, StageExecution


class DiagnoseIncidentStatusTests(TestCase):
    def setUp(self):
        # No pipeline => all four stages expected (un-routed fallback).
        self.incident = Incident.objects.create(title="Statuses")
        self.run = PipelineRun.objects.create(
            trace_id="t1", run_id="r1", incident=self.incident
        )

    def _exec(self, stage, status, **kw):
        return StageExecution.objects.create(
            pipeline_run=self.run, stage=stage, status=status, **kw
        )

    def _entry(self, incident, stage):
        return {e["stage"]: e for e in diagnose_incident(incident)}[stage]

    def test_succeeded_with_output_is_ok(self):
        self._exec("notify", "succeeded", output_ref="ref://msg/1")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_succeeded_with_snapshot_is_ok(self):
        self._exec("notify", "succeeded", output_snapshot={"sent": 1})
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_succeeded_no_output_is_empty(self):
        self._exec("notify", "succeeded")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "empty")

    def test_succeeded_but_run_level_ref_present_is_ok(self):
        self.run.notify_output_ref = "ref://delivery/1"
        self.run.save(update_fields=["notify_output_ref"])
        self._exec("notify", "succeeded")
        self.assertEqual(self._entry(self.incident, "notify")["status"], "ok")

    def test_failed_reports_error_and_retryable(self):
        self._exec(
            "analyze", "failed", error_type="Timeout",
            error_message="provider 504", error_retryable=True,
        )
        entry = self._entry(self.incident, "analyze")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("provider 504", entry["detail"])
        self.assertIn("retryable=True", entry["detail"])

    def test_running_is_stalled(self):
        self._exec("check", "running")
        self.assertEqual(self._entry(self.incident, "check")["status"], "stalled")

    def test_pending_is_stalled(self):
        self._exec("check", "pending")
        self.assertEqual(self._entry(self.incident, "check")["status"], "stalled")

    def test_explicit_skipped_execution_reports_reason(self):
        self._exec("check", "skipped", error_message="Skipped: diagnostics inline")
        entry = self._entry(self.incident, "check")
        self.assertEqual(entry["status"], "skipped")
        self.assertEqual(entry["detail"], "diagnostics inline")

    def test_explicit_skipped_without_reason(self):
        self._exec("check", "skipped", error_message="")
        self.assertEqual(self._entry(self.incident, "check")["detail"], "no reason recorded")
```

**Step 2: Run to verify**

Run: `uv run pytest apps/alerts/_tests/test_diagnosis.py::DiagnoseIncidentStatusTests -v`
Expected: PASS. If any fail, the defect is in `diagnosis.py` from Task 1 — fix it there and re-run.

**Step 3: Commit**

```bash
git add apps/alerts/_tests/test_diagnosis.py apps/alerts/diagnosis.py
git commit -m "test(alerts): cover diagnose_incident status classification branches"
```

---

## Task 3: Multi-run aggregation & rollup

Prove latest-run-wins and the `succeeded in N/M runs` rollup across multiple runs.

**Files:**
- Test: `apps/alerts/_tests/test_diagnosis.py`

**Step 1: Write the failing tests**

Add to `apps/alerts/_tests/test_diagnosis.py`:

```python
class DiagnoseIncidentAggregationTests(TestCase):
    def _entry(self, incident, stage):
        return {e["stage"]: e for e in diagnose_incident(incident)}[stage]

    def test_latest_run_wins_and_rollup_counts(self):
        incident = Incident.objects.create(title="Multi")
        # Older run: notify succeeded. Newer run: notify failed.
        old = PipelineRun.objects.create(trace_id="t1", run_id="r1", incident=incident)
        StageExecution.objects.create(
            pipeline_run=old, stage="notify", status="succeeded", output_ref="ref://1"
        )
        new = PipelineRun.objects.create(trace_id="t2", run_id="r2", incident=incident)
        StageExecution.objects.create(
            pipeline_run=new, stage="notify", status="failed",
            error_type="X", error_message="boom", error_retryable=False,
        )
        entry = self._entry(incident, "notify")
        self.assertEqual(entry["status"], "failed")           # latest run wins
        self.assertEqual(entry["runs"], "succeeded in 1/2 runs")

    def test_highest_attempt_within_latest_run_wins(self):
        incident = Incident.objects.create(title="Retry")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run, stage="analyze", status="failed", attempt=1,
            error_type="X", error_message="first", error_retryable=True,
        )
        StageExecution.objects.create(
            pipeline_run=run, stage="analyze", status="succeeded", attempt=2,
            output_ref="ref://ok",
        )
        self.assertEqual(self._entry(incident, "analyze")["status"], "ok")
```

**Step 2: Run to verify**

Run: `uv run pytest apps/alerts/_tests/test_diagnosis.py::DiagnoseIncidentAggregationTests -v`
Expected: PASS.

**Step 3: Confirm full classifier coverage**

Run:
```bash
uv run coverage run -m pytest apps/alerts/_tests/test_diagnosis.py
uv run coverage report --include="*/apps/alerts/diagnosis.py"
```
Expected: 100% for `diagnosis.py`. If a branch is uncovered, add a targeted test.

**Step 4: Commit**

```bash
git add apps/alerts/_tests/test_diagnosis.py
git commit -m "test(alerts): cover diagnose_incident multi-run aggregation"
```

---

## Task 4: Render the strip in the incident admin page

Add `IncidentAdmin.diagnosis_display` and wire it into the Journey fieldset, above `journey_display`.

**Files:**
- Modify: `apps/alerts/admin.py` (`IncidentAdmin`: `readonly_fields` ~line 249, `fieldsets` Journey block ~line 296, add method near `journey_display` ~line 418)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing test**

Add to `apps/alerts/_tests/test_admin.py` (follow the file's existing admin-test style; if it uses a `RequestFactory`/site fixture, reuse it — otherwise call the method directly as below):

```python
class IncidentDiagnosisDisplayTests(TestCase):
    def test_diagnosis_display_renders_stage_rows(self):
        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident
        from django.contrib.admin.sites import AdminSite

        incident = Incident.objects.create(title="D")
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        # All four stage labels present; no runs => never ran shown.
        for label in ("alerts", "checkers", "intelligence", "notify"):
            self.assertIn(label, html)
        self.assertIn("never", html.lower())

    def test_diagnosis_display_escapes_detail(self):
        from apps.alerts.admin import IncidentAdmin
        from apps.alerts.models import Incident
        from apps.orchestration.models import PipelineRun, StageExecution
        from django.contrib.admin.sites import AdminSite

        incident = Incident.objects.create(title="D2")
        run = PipelineRun.objects.create(trace_id="t", run_id="r", incident=incident)
        StageExecution.objects.create(
            pipeline_run=run, stage="analyze", status="failed",
            error_type="X", error_message="<script>bad</script>", error_retryable=False,
        )
        admin = IncidentAdmin(Incident, AdminSite())
        html = str(admin.diagnosis_display(incident))
        self.assertNotIn("<script>bad", html)
        self.assertIn("&lt;script&gt;", html)
```

**Step 2: Run to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::IncidentDiagnosisDisplayTests -v`
Expected: FAIL — `AttributeError: 'IncidentAdmin' object has no attribute 'diagnosis_display'`.

**Step 3: Implement**

In `apps/alerts/admin.py`, add the import near the top (with the other `apps.alerts` imports):

```python
from apps.alerts.diagnosis import diagnose_incident
```

Add the method to `IncidentAdmin`, next to `journey_display` (~line 418):

```python
_STAGE_LABELS = {
    "ingest": "alerts",
    "check": "checkers",
    "analyze": "intelligence",
    "notify": "notify",
}
_STATUS_GLYPH = {
    "ok": ("✓", "#2e7d32"),
    "empty": ("✓→∅", "#b26a00"),
    "failed": ("✗", "#b00020"),
    "stalled": ("…", "#b26a00"),
    "skipped": ("⊘", "#888"),
    "never_ran": ("✗", "#b00020"),
}

@admin.display(description="Stage diagnosis (expected vs actual)")
def diagnosis_display(self, obj):
    """Compact expected-vs-actual stage strip. All dynamic values escaped."""
    entries = diagnose_incident(obj)
    rows = format_html_join(
        "",
        '<li><b style="display:inline-block;width:90px;">{}</b>'
        '<span style="color:{};">{} {}</span>{}{}</li>',
        (
            (
                self._STAGE_LABELS.get(e["stage"], e["stage"]),
                self._STATUS_GLYPH.get(e["status"], ("?", "#888"))[1],
                self._STATUS_GLYPH.get(e["status"], ("?", "#888"))[0],
                e["status"].replace("_", " "),
                format_html(" — {}", e["detail"]) if e.get("detail") else "",
                format_html(
                    ' <span style="color:#888;">({})</span>', e["runs"]
                )
                if e.get("runs")
                else "",
            )
            for e in entries
        ),
    )
    return format_html('<ul style="margin:0 0 0 16px;list-style:none;padding:0;">{}</ul>', rows)
```

Wire it in: add `"diagnosis_display"` to `IncidentAdmin.readonly_fields`, and put it first in the Journey fieldset `fields` list:

```python
"fields": ["diagnosis_display", "journey_display", "journey_timeline"],
```

Confirm `format_html_join` and `format_html` are already imported in `admin.py` (they are — used by `journey_timeline`).

**Step 4: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_admin.py::IncidentDiagnosisDisplayTests -v`
Expected: PASS (2 tests).

**Step 5: Full suite + lint**

Run:
```bash
uv run pytest apps/alerts/
uv run black apps/alerts/diagnosis.py apps/alerts/admin.py apps/alerts/_tests/test_diagnosis.py apps/alerts/_tests/test_admin.py
uv run ruff check apps/alerts/
```
Expected: all pass; black clean; ruff clean.

**Step 6: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat(alerts): render stage-diagnosis strip on the incident admin page"
```

---

## Acceptance criteria

- Opening an incident shows a per-stage strip labelled alerts / checkers / intelligence / notify, each with one of ok / empty / failed / stalled / skipped / never-ran and (where relevant) a reason and an `N/M runs` rollup.
- A flag-disabled stage reads `skipped — config: run_… disabled`, never `never_ran`.
- Latest-run status wins; rollup counts succeeded runs correctly.
- Detail strings derived from payloads/errors are HTML-escaped.
- `diagnosis.py` has 100% branch coverage; `uv run pytest apps/alerts/` is green; black + ruff clean.
- No new URL, view, app, model, or template introduced.

## Out of scope (per design)

Standalone page, CLI command, per-run matrix, whole-system rollup view, and `CheckRun`/
message-count emptiness — all deferrable on top of the same pure function. The per-run
`skip_checkers` override is a documented limitation, not handled here.
