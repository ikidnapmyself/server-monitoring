---
title: "Incidents as the Pipeline Subject: Implementation"
parent: Plans
---

# Incidents as the Pipeline Subject Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make orchestration handle incidents and nothing else, by retiring INGEST as a stage and turning the synchronous drain into a mode of the one path.

**Architecture:** Every producer (webhook, node push, local checkers, operator transition) writes alerts, lets incidents form, and enqueues one run per materially changed incident through a single shared helper. Whether the caller drains those runs before returning is a flag on that helper, not a different code path. Once every producer uses it, the entry-stage machinery in `_execute_pipeline` is deleted.

**Tech Stack:** Django 5, pytest + pytest-django, uv.

**Design doc:** `docs/plans/2026-08-29-incidents-as-pipeline-subject-design.md` (merged, PR #222).

---

## Assumptions taken, flagged for correction

Two decisions were raised in the design PR and not answered. The cheaper, reversible option is taken for both. Change these here if wrong, before starting.

1. **`PipelineRun.incident` stays nullable in the database and becomes required in code.** No backfill of historical rows, no migration. A run created without an incident is a programming error caught by a test, not a database constraint.
2. **A payload producing no alerts leaves no run.** `Alert.raw_payload` holds the body for every alert that was created. A payload that parsed to nothing is a misconfigured sender, so it is logged at WARNING with the driver name and the trace id rather than given a row.

## Order matters

Tasks 1 to 6 add the new path and move producers onto it one at a time. The suite stays green throughout and behaviour is unchanged until Task 3. Task 7 deletes the old machinery and is the payoff. Do not reorder.

## Legacy runs at deploy time

`IngestExecutor` and the ingest branch survive this plan. A `PENDING` run recorded by the old webhook before the deploy still carries `{driver, payload}` and must still drain. Task 7 keeps that branch alive with a comment marking it legacy-only. Deleting it is a follow-up, once no such rows remain.

---

## Task 1: One enqueue helper for every producer

**Files:**
- Create: `apps/orchestration/intake.py`
- Test: `apps/orchestration/_tests/test_intake.py`

`ProcessingResult` (`apps/alerts/services.py`) and `CheckAlertResult` (`apps/alerts/check_integration.py`) both expose `material_alerts`, so one helper serves the webhook, a node push, and local checkers.

**Step 1: Write the failing test**

```python
"""Tests for the shared producer intake."""

import pytest

from apps.alerts.models import Alert, AlertStatus, Incident
from apps.orchestration.intake import enqueue_for
from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus


class _Result:
    """Minimal stand-in: any producer result exposing material_alerts."""

    def __init__(self, material_alerts):
        self.material_alerts = material_alerts


@pytest.mark.django_db
def test_enqueues_one_pending_run_per_material_incident():
    incident = Incident.objects.create(title="cpu", severity="critical")
    alert = Alert.objects.create(
        fingerprint="check:n1:cpu", source="cluster", name="CPU Check Alert",
        severity="critical", status=AlertStatus.FIRING, incident=incident,
    )
    runs = enqueue_for(_Result([alert]), trace_id="t1", origin=PipelineOrigin.INCOMING_WEBHOOK)

    assert len(runs) == 1
    assert runs[0].status == PipelineStatus.PENDING
    assert runs[0].incident_id == incident.id
    assert runs[0].trace_id == "t1"


@pytest.mark.django_db
def test_an_alert_with_no_incident_enqueues_nothing():
    alert = Alert.objects.create(
        fingerprint="check:n1:cpu", source="cluster", name="CPU Check Alert",
        severity="warning", status=AlertStatus.FIRING,
    )
    assert enqueue_for(_Result([alert]), trace_id="t1", origin=PipelineOrigin.MANUAL) == []


@pytest.mark.django_db
def test_no_material_alerts_enqueues_nothing():
    assert enqueue_for(_Result([]), trace_id="t1", origin=PipelineOrigin.MANUAL) == []
    assert PipelineRun.objects.count() == 0


@pytest.mark.django_db
def test_sync_drains_the_runs_it_enqueued():
    # sync=True must leave no PENDING run behind: the caller expects one call
    # to finish the job. Assert on status, not on side effects of the lane.
    ...
```

Write the fourth test against a real incident whose lane resolves; follow the fixture style in `apps/orchestration/_tests/test_inbox.py`.

**Step 2: Run and confirm it fails**

Run: `uv run pytest apps/orchestration/_tests/test_intake.py -v`
Expected: `ModuleNotFoundError: No module named 'apps.orchestration.intake'`

**Step 3: Implement**

```python
"""Where a producer hands work to orchestration.

Every producer does the same two things: write alerts and let incidents form,
then enqueue one run per incident that materially changed. This is the second
half, shared by the webhook, a node push, local checkers, and an operator
transition, so there is exactly one way work enters the pipeline.

Whether the caller drains those runs before returning is a mode, not a
different path. A synchronous caller (``check_health``, an operator looking at
a machine over SSH) drains its own; a hub leaves them for ``process_inbox``.
Same rows, same lanes, same executors either way.
"""

import logging

from apps.orchestration.models import PipelineRun

logger = logging.getLogger(__name__)


def enqueue_for(
    result,
    *,
    trace_id: str,
    origin: str,
    source: str = "",
    environment: str = "",
    node=None,
    max_retries: int = 3,
    sync: bool = False,
) -> list[PipelineRun]:
    """Enqueue one run per materially changed incident in ``result``.

    ``result`` is any producer result exposing ``material_alerts`` — both
    ``ProcessingResult`` and ``CheckAlertResult`` do. Returns the runs created,
    empty when nothing changed materially.

    With ``sync=True`` the runs are drained before returning.
    """
    from apps.orchestration.inbox import drain_runs, enqueue_incident_runs
    from apps.orchestration.routing import material_incident_ids

    incident_ids = material_incident_ids(getattr(result, "material_alerts", []))
    if not incident_ids:
        return []

    runs = enqueue_incident_runs(
        incident_ids,
        trace_id=trace_id,
        origin=origin,
        source=source,
        environment=environment,
        node=node,
        max_retries=max_retries,
    )
    if sync:
        drain_runs(runs)
    return runs
```

**Step 4: Run the tests, then the app suites**

```
uv run pytest apps/orchestration/_tests/test_intake.py -v
uv run pytest apps/orchestration apps/alerts -q
```

**Step 5: Commit**

```bash
git add apps/orchestration/intake.py apps/orchestration/_tests/test_intake.py
git commit -m "feat(orchestration): one intake helper for every producer"
```

---

## Task 2: Draining a known set of runs

**Files:**
- Modify: `apps/orchestration/inbox.py`
- Modify: `apps/orchestration/orchestrator.py` (the self-drain block, around lines 255-266)
- Test: `apps/orchestration/_tests/test_inbox.py`

`PipelineOrchestrator.run_pipeline` already drains the children it enqueued, with a comment explaining that synchronous callers expect one call to carry the whole pipeline through. That logic becomes the shared mode instead of one command's special case.

**Step 1: Write the failing test**

```python
@pytest.mark.django_db
def test_drain_runs_executes_exactly_the_runs_given():
    # a PENDING run NOT in the list must still be PENDING afterwards
```

Also assert it claims the same way `drain` does, so a concurrent `process_inbox` cannot double-execute.

**Step 2: Run, confirm `drain_runs` does not exist.**

**Step 3: Implement `drain_runs(runs)` in `apps/orchestration/inbox.py`**

Lift the body of the self-drain block from `orchestrator.py`: iterate the run pks, `claim(pk)`, and execute. Keep the existing comment about why `execute_run()` does not self-drain (`process_inbox` is already the drain and would recurse).

Then replace that block in `run_pipeline` with a call to `drain_runs`, so there is one implementation.

**Step 4: Run** `uv run pytest apps/orchestration -q`, then the full suite. Behaviour is unchanged; this is a move.

**Step 5: Commit**

```bash
git commit -m "refactor(orchestration): draining a known set of runs is shared"
```

---

## Task 3: The webhook ingests inline

**Files:**
- Modify: `apps/alerts/views.py` (the `post` method, around lines 57-85)
- Test: `apps/alerts/_tests/views/test_webhook.py`

This is the first behaviour change. The webhook stops recording an ingest run and instead writes alerts, lets incidents form, and enqueues incident runs.

The rule the view states is "no inline pipeline". What moves onto the request thread is bounded alert writes, not checkers, inference, or delivery, and concurrency is already capped by worker count.

**Step 1: Write the failing tests**

```python
def test_webhook_creates_alerts_synchronously(self):
    # POST a cluster payload; an Alert row exists before any drain runs

def test_webhook_enqueues_one_run_per_material_incident(self):
    # PipelineRun rows are PENDING and carry incident_id

def test_webhook_records_no_ingest_run(self):
    # no run carries a {"driver": ...} payload any more

def test_webhook_still_returns_202(self):
    # the response shape stays: status accepted

def test_a_payload_over_the_cap_is_rejected(self):
    # 413 or 400, and nothing is written

def test_a_payload_that_parses_to_no_alerts_is_logged_not_recorded(self):
    # no run, no alert, one WARNING naming the driver and trace id
```

**Step 2: Run, confirm the ingest-run assertions fail.**

**Step 3: Implement**

Replace the `start_pipeline` call with:

```python
MAX_ALERTS_PER_PAYLOAD = 500  # module level, with a comment

trace_id = str(uuid.uuid4())
orchestrator = AlertOrchestrator(trace_id=trace_id)
proc = orchestrator.process_webhook(payload, driver=driver)
runs = enqueue_for(
    proc,
    trace_id=trace_id,
    origin=PipelineOrigin.INCOMING_WEBHOOK,
    source=driver or "unknown",
)
```

Reject before parsing when the payload's alert list exceeds the cap. Keep `register_pushing_node` exactly where it is; it still runs first.

Return 202 with `trace_id` and the incident ids rather than a `run_id`. Check every caller and test that reads `run_id` from this response before changing the shape.

**Step 4: Run** `uv run pytest apps/alerts apps/orchestration -q`, then the full suite. Expect failures in tests that assert an ingest run was recorded; those are the point. Update them to the new expectation, never weaken them.

**Step 5: Commit**

```bash
git commit -m "feat(alerts): the webhook ingests and enqueues, it does not record a stage"
```

---

## Task 4: `push_to_hub --local` uses the intake

**Files:**
- Modify: `apps/alerts/management/commands/push_to_hub.py` (`_record_local`)
- Test: `apps/alerts/_tests/commands/test_push_to_hub.py`

`--local` currently records a run carrying a cluster payload, so it drains through `IngestExecutor`. It should instead run the checkers it already ran, write their alerts through the bridge, and enqueue incident runs. No HTTP, no wrapper payload.

Assert the end-to-end result is unchanged: the same Alert rows with the same fingerprints. Keep `--json` emitting the same keys, replacing `run_id` with the trace id if the run is no longer singular.

**Commit:** `refactor(alerts): --local produces truth and enqueues, like every producer`

---

## Task 5: `check_health` drains its own incidents

**Files:**
- Modify: `apps/checkers/management/commands/check_health.py` (`_record_alerts`)
- Test: `apps/checkers/_tests/test_commands.py`

This is the decision from the design: one local entrypoint that produces truth and completes the orchestration synchronously. Real-time checkup and analysis, no daemon.

```python
runs = enqueue_for(
    bridge_result,
    trace_id=trace_id,
    origin=PipelineOrigin.CHECKER_GENERATED,
    source=CheckAlertBridge.SOURCE_NAME,
    sync=True,
)
```

Tests must cover:
- alerts and incidents still written (unchanged)
- the incidents are analyzed in the same call, so nothing is left PENDING
- `--no-alert` still writes nothing and enqueues nothing
- a failure to drain does not change the command's exit code or its printed output, matching the existing `_record_alerts` contract
- with no `NotificationChannel` active, nothing is delivered

Add `--no-notify`, passed through so a look at a machine pages nobody. Preserve the existing behaviour where alert-recording failures are reported on stderr and logged, never raised.

**Commit:** `feat(checkers): check_health completes the orchestration synchronously`

---

## Task 6: `run_pipeline --checks-only` delegates and deprecates

**Files:**
- Modify: `apps/orchestration/management/commands/run_pipeline.py`
- Test: `apps/orchestration/_tests/test_run_pipeline_command.py`

`--checks-only` becomes a thin wrapper that prints a deprecation notice naming `check_health` and does the same work through the same intake. Do not delete it; the operator uses it over SSH and a removed command is a worse surprise than a warning.

`run_pipeline` keeps `--sample`, `--payload` and `--file`, which is what it is really for: replaying a webhook-shaped payload. Those go through the same intake as the webhook.

**Commit:** `refactor(orchestration): --checks-only delegates to the shared intake`

---

## Task 7: Delete the entry-stage machinery

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (`_execute_pipeline`, roughly lines 353-500)

The payoff. Every run now has an incident, so the three-way branch has one case left.

Delete: the `checks_only` branch and its status handling, `run_routes` and the `--no-incidents` carve-out, `entry_stage` and the rule that only the entry stage may route, the fan-out block inside the stage loop, and the legacy-snapshot compatibility fallback.

**Keep, with a comment marking it legacy-only:** the branch that handles a run whose payload carries `{driver, payload}`. A `PENDING` run recorded by the old webhook before this deploy must still drain. Add a test that pins this, and a note that the branch can go once no such rows remain.

Routing resolves from the run's incident, once, at the top. There is no longer a stage that "produces the subject".

**Step 1:** Before deleting anything, run the full suite and record the count. Every test that passes now must pass after.

**Step 2:** Delete in one commit, run the suite, and read every failure carefully. A failure here means a caller still depends on the old shape, which is information, not noise.

**Commit:** `refactor(orchestration): a run is an incident, and nothing else`

---

## Task 8: `PipelineRun.incident` is required in code

**Files:**
- Modify: `apps/orchestration/inbox.py`, `apps/orchestration/orchestrator.py`
- Test: `apps/orchestration/_tests/test_inbox.py`

No migration and no database constraint, per the assumption at the top. Enqueue paths already set it; add an explicit guard so a future caller cannot create a subject-less run, and a test that the guard fires.

**Commit:** `feat(orchestration): every run has an incident subject`

---

## Task 9: INGEST stops being expected

**Files:**
- Modify: `apps/alerts/diagnosis.py` (`_STAGE_ORDER`, `_is_expected`)
- Modify: `apps/orchestration/models.py` (the `PipelineStage` docstring)
- Test: `apps/alerts/_tests/test_diagnosis.py`

`_is_expected` treats INGEST as always expected, which would show every new incident as missing a stage it will never run. It becomes expected only for runs recorded before this change, which in practice means: expected when the incident has a run carrying a legacy ingest payload.

Keep `PipelineStage.INGEST` in the enum. Historical `StageExecution` rows reference it and must keep rendering in the admin.

**Commit:** `fix(alerts): INGEST is history, not an expected stage`

---

## Task 10: Docs

**Files:**
- Modify: `AGENTS.md` (the pipeline flow and the core rule)
- Modify: `apps/orchestration/AGENTS.md`, `apps/alerts/AGENTS.md`, `apps/checkers/AGENTS.md`
- Modify: `docs/Architecture.md`, `docs/Index.md`

The root `AGENTS.md` documents `alerts → checkers → intelligence → notify` with alerts as stage one, and its mental-model pseudocode starts with `alerts.ingest()`. Both change: producers write truth, orchestration runs incidents, stages are diagnose, analyze, notify.

Keep the hard boundary rule as it is. It is unaffected and still true: only the orchestrator advances a run.

**Commit:** `docs: producers write truth, orchestration runs incidents`

---

## Verification before the branch is done

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run python manage.py makemigrations --check --dry-run
uv run bandit -r apps/ config/ -c pyproject.toml
./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/
```

Manual pass on a scratch copy, covering both topologies:

```bash
# solo node: one command, whole pipeline, no daemon
uv run python manage.py check_health cpu --no-notify
uv run python manage.py trace <trace_id>

# hub: a push queues, the drain completes it
curl -X POST .../alerts/webhook/cluster/ -d @payload.json
uv run python manage.py process_inbox
```

Confirm: no run carries a `{driver, payload}` wrapper, every run has an incident, `check_health` leaves nothing PENDING, and the webhook leaves exactly one PENDING run per changed incident.

## What must not regress

- Hub with agents: nightly pushes, queued drain, per-node config and re-evaluation
- Solo node: `check_health` with no hub, no cron, no daemon
- Durable ingest under flood: a burst must not run checkers, inference or delivery inline
- Operator transitions through `IncidentManager`
- `--no-notify`
