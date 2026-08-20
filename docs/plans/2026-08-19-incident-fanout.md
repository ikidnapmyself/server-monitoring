---
title: "Incident Fan-out Implementation Plan"
parent: Plans
---

{% raw %}

# Incident Fan-out Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop dropping N-1 incidents per push — give every materially-changed incident its own downstream pipeline run, and suppress unchanged ones.

**Architecture:** The push run keeps INGEST/CHECK and stops carrying a single "subject" incident downstream. Instead, as alerts are written, both ingest paths record which incidents *materially changed*; after the entry stage the run enqueues one PENDING `PipelineRun` per such incident, which `process_inbox` drains. Each downstream run resolves its own lane from its own incident and runs only that lane's stages.

**Tech Stack:** Django 5.2, SQLite, pytest + pytest-django, `uv` for all commands.

**Design doc:** `docs/plans/2026-08-19-incident-fanout-design.md`

**Branch:** `design/incident-fanout` (already checked out; design doc already committed as `fff7549`).

---

## Context the executor needs

Read the design doc first. Then the following, which the design does not spell out.

### Two corrections to the design doc

1. **The context key is NOT a `BaseChecker` method.** Checkers run on nodes; the gate runs on
   the hub over `Alert` rows, so the hub never has the checker object. The seam is a registry
   keyed by the `checker` label — exactly like `apps/alerts/reevaluation.py`'s `SCORERS` /
   `REEVALUATORS` dicts. Metrics are already available hub-side: `CheckAlertBridge` writes them
   into `annotations` (`apps/alerts/check_integration.py:161-163`) and
   `reevaluation._metrics` reads them back with `parse_metrics(parsed.annotations)`
   (`reevaluation.py:46-47`). This also means **no node redeploy is required**.
2. **Materiality is decided inside the alert write path, not after it.** By the time an executor
   sees `ProcessingResult.alerts`, the old severity/status/context key have already been
   overwritten. So `_update_alert` compares and records, in both paths.

### Existing code this plan touches

- `apps/alerts/services.py` — `AlertOrchestrator`. `ProcessingResult` (`:112-131`),
  `_create_alert` (`:263`), `_update_alert` (`:322`), `_diff_alert` (`:312`).
- `apps/alerts/check_integration.py` — `CheckAlertBridge`, a **second, parallel** create/update
  path for checker traffic. `_create_alert` (~`:270`), `_update_alert` (`:309-345`),
  annotations built at `:161-163`. Anything added to one path must be added to the other; that
  duplication is a known hazard.
- `apps/orchestration/orchestrator.py` — `_execute_pipeline` (`:283`), entry-stage selection
  (`:325-352`), `route_from_entry_stage` (`:369-397`), the stage loop (`:399+`),
  `_downstream_stages` (`:600`), `_downstream_or_fail` (`:646`), `_final_status` (`:683`),
  `_legacy_subject_alert_id` (`:595`), `execute_run` (`:274`), `start_pipeline` (`:169`).
- `apps/orchestration/inbox.py` — `claim` (`:44`), `drain` (`:59`), `drain_run` (`:77`).
- `apps/orchestration/models.py` — `PipelineOrigin` (`:23`), `PipelineRun` (`:56`),
  `PipelineDefinition` (`:486`).
- `apps/orchestration/migrations/0012_seed_default_lanes.py` — the pattern for seeding a lane
  with `get_or_create` plus a conservative `backwards`.

### Verify commands (run after every task)

```bash
uv run pytest apps/alerts/_tests/ apps/orchestration/_tests/ -q
uv run black . && uv run ruff check . && uv run python manage.py check
```

### Conventions

Absolute imports only. Line length 100. 100% branch coverage on changed code. Commit after every
task.

---

## Task 1: `Alert.context_key` field

**Files:**
- Modify: `apps/alerts/models.py` (the `Alert` model, beside `fingerprint`)
- Create: `apps/alerts/migrations/00XX_alert_context_key.py` (generated)
- Test: `apps/alerts/_tests/test_models.py`

**Step 1: Write the failing test**

```python
def test_alert_context_key_defaults_to_empty(self):
    alert = Alert.objects.create(
        fingerprint="ck-default",
        source="test",
        name="CPU Check Alert",
        severity=AlertSeverity.WARNING,
        status=AlertStatus.FIRING,
    )
    self.assertEqual(alert.context_key, "")
```

**Step 2: Run it and watch it fail**

```bash
uv run pytest apps/alerts/_tests/test_models.py -k context_key -v
```

Expected: `AttributeError` / `TypeError` — no such field.

**Step 3: Add the field**

In `apps/alerts/models.py`, immediately after the `fingerprint` field on `Alert`:

```python
    context_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Stable digest of the situation this alert describes, used to decide "
            "whether a re-push is materially new. Empty means severity and status "
            "alone decide. See apps.alerts.context_keys."
        ),
    )
```

**Step 4: Generate and apply the migration**

```bash
uv run python manage.py makemigrations alerts -n alert_context_key
uv run python manage.py migrate
```

**Step 5: Run the test**

```bash
uv run pytest apps/alerts/_tests/test_models.py -k context_key -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations apps/alerts/_tests/test_models.py
git commit -m "feat(alerts): add Alert.context_key for the fan-out change gate"
```

---

## Task 2: The context-key registry

A pure module: given a checker name and an alert's annotations, return a stable string. No
registry entry means an empty key, so severity and status alone decide.

**Files:**
- Create: `apps/alerts/context_keys.py`
- Test: `apps/alerts/_tests/test_context_keys.py`

**Step 1: Write the failing tests**

```python
from django.test import SimpleTestCase

from apps.alerts.context_keys import context_key_for


class ContextKeyTests(SimpleTestCase):
    def test_unregistered_checker_has_no_key(self):
        self.assertEqual(context_key_for("cpu", {"percent": "91.2"}), "")

    def test_missing_checker_label_has_no_key(self):
        self.assertEqual(context_key_for("", {}), "")

    def test_listening_ports_key_is_the_sorted_unexpected_set(self):
        annotations = {"unexpected_ports": "[8080, 22]"}
        self.assertEqual(context_key_for("listening_ports", annotations), "22,8080")

    def test_listening_ports_key_is_order_independent(self):
        first = context_key_for("listening_ports", {"unexpected_ports": "[22, 8080]"})
        second = context_key_for("listening_ports", {"unexpected_ports": "[8080, 22]"})
        self.assertEqual(first, second)

    def test_listening_ports_with_no_flagged_ports(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "[]"}), "")

    def test_unparseable_metrics_fall_back_to_no_key(self):
        self.assertEqual(context_key_for("listening_ports", {"unexpected_ports": "junk"}), "")

    def test_non_dict_annotations_are_safe(self):
        self.assertEqual(context_key_for("listening_ports", None), "")
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_context_keys.py -v
```

Expected: `ModuleNotFoundError: apps.alerts.context_keys`.

**Step 3: Write the module**

```python
"""Per-checker "what situation is this?" keys for the fan-out change gate.

The gate must not compare free text: for checker traffic ``description`` is
``CheckResult.message`` (``apps/alerts/check_integration.py:158``), which carries
live metric values and changes on nearly every push. Severity and status alone are
too coarse for some checkers — a new unexpected port at unchanged WARNING is real
news — so those checkers name the part of their metrics that identifies the
situation.

This is a hub-side registry, NOT a method on ``BaseChecker``: checkers run on nodes,
and the hub only ever sees the resulting ``Alert``. It mirrors
``apps.alerts.reevaluation.SCORERS``, reads metrics from annotations the same way,
and needs no node-side change.
"""

import logging
from collections.abc import Callable

from apps.alerts.metrics import parse_metrics

logger = logging.getLogger(__name__)


def _listening_ports_key(metrics: dict) -> str:
    """The sorted set of flagged ports. Empty when nothing is flagged."""
    ports = metrics.get("unexpected_ports")
    if not isinstance(ports, list):
        return ""
    numbers = sorted({p for p in ports if isinstance(p, int) and not isinstance(p, bool)})
    return ",".join(str(p) for p in numbers)


#: checker name -> (metrics) -> key. A checker with no entry has no key, which means
#: severity and status alone decide whether its re-push is material.
CONTEXT_KEYS: dict[str, Callable[[dict], str]] = {
    "listening_ports": _listening_ports_key,
}


def context_key_for(checker: str, annotations: object) -> str:
    """Stable key for this alert's situation, or "" when there is nothing to compare.

    Fails **open** (returns ""): a checker with no entry, unparseable annotations or a
    raising builder all degrade to severity/status-only gating, which over-notifies
    rather than silencing. Silence is the dangerous direction here.
    """
    builder = CONTEXT_KEYS.get(checker or "")
    if builder is None:
        return ""
    if not isinstance(annotations, dict):
        return ""
    metrics = parse_metrics(annotations)
    if not isinstance(metrics, dict):
        return ""
    try:
        return builder(metrics)
    except Exception:  # pragma: no cover - defensive; a bad key must not break ingest
        logger.exception("context_key builder failed for checker %r", checker)
        return ""
```

**Step 4: Confirm `parse_metrics`' import path and behaviour before relying on it**

```bash
uv run python -c "from apps.alerts.reevaluation import parse_metrics; print(parse_metrics.__module__)"
```

If it does not live in `apps.alerts.metrics`, fix the import in the module above to match, and
check what it returns for an unparseable value — the test
`test_unparseable_metrics_fall_back_to_no_key` encodes the assumption that a junk value does not
become a list. Adjust the test to the real behaviour if it differs, keeping the "fails open"
property.

**Step 5: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_context_keys.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/alerts/context_keys.py apps/alerts/_tests/test_context_keys.py
git commit -m "feat(alerts): hub-side per-checker context keys for the change gate"
```

---

## Task 3: The materiality predicate

One predicate, called by both write paths, so webhook and checker traffic can never gate
differently.

**Files:**
- Create: `apps/alerts/materiality.py`
- Test: `apps/alerts/_tests/test_materiality.py`

**Step 1: Write the failing tests**

```python
from django.test import SimpleTestCase

from apps.alerts.materiality import is_material_change


class MaterialityTests(SimpleTestCase):
    def test_unchanged_is_not_material(self):
        self.assertFalse(
            is_material_change(
                old_severity="warning", new_severity="warning",
                old_status="firing", new_status="firing",
                old_key="22", new_key="22",
            )
        )

    def test_severity_escalation_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning", new_severity="critical",
                old_status="firing", new_status="firing",
                old_key="", new_key="",
            )
        )

    def test_severity_de_escalation_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="critical", new_severity="warning",
                old_status="firing", new_status="firing",
                old_key="", new_key="",
            )
        )

    def test_resolution_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning", new_severity="warning",
                old_status="firing", new_status="resolved",
                old_key="", new_key="",
            )
        )

    def test_refire_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning", new_severity="warning",
                old_status="resolved", new_status="firing",
                old_key="", new_key="",
            )
        )

    def test_context_key_change_is_material(self):
        self.assertTrue(
            is_material_change(
                old_severity="warning", new_severity="warning",
                old_status="firing", new_status="firing",
                old_key="22", new_key="22,8080",
            )
        )
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_materiality.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write the module**

```python
"""One rule for "has this alert materially changed?", shared by both ingest paths.

``AlertOrchestrator`` and ``CheckAlertBridge`` are separate create/update paths that
already record different history events (``refired``/``updated`` vs
``severity_changed``). A gate built on those events would behave differently by
origin, and checker traffic — the case the gate exists for — is on the bridge side.
So the predicate lives here and both paths call it.

Deliberately excluded: ``description``. For checker alerts it is
``CheckResult.message``, which carries live metric values and would make every push
look material.
"""


def is_material_change(
    *,
    old_severity: str,
    new_severity: str,
    old_status: str,
    new_status: str,
    old_key: str,
    new_key: str,
) -> bool:
    """True when this update deserves its own downstream pipeline run."""
    return (
        old_severity != new_severity
        or old_status != new_status
        or (old_key or "") != (new_key or "")
    )
```

**Step 4: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_materiality.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/materiality.py apps/alerts/_tests/test_materiality.py
git commit -m "feat(alerts): shared materiality predicate for the change gate"
```

---

## Task 4: Record materiality in `AlertOrchestrator` (webhook path)

**Files:**
- Modify: `apps/alerts/services.py` — `ProcessingResult`, `_create_alert`, `_update_alert`
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing tests**

Add to `AlertOrchestratorTests` in `apps/alerts/_tests/test_services.py`:

```python
def test_new_alert_is_material(self):
    result = self.orchestrator.process_webhook(self.alertmanager_payload)
    self.assertEqual(len(result.material_alerts), 1)

def test_identical_repush_is_not_material(self):
    self.orchestrator.process_webhook(self.alertmanager_payload)
    result = self.orchestrator.process_webhook(self.alertmanager_payload)
    self.assertEqual(result.material_alerts, [])

def test_severity_change_is_material(self):
    self.orchestrator.process_webhook(self.alertmanager_payload)
    escalated = copy.deepcopy(self.alertmanager_payload)
    escalated["alerts"][0]["labels"]["severity"] = "critical"
    result = self.orchestrator.process_webhook(escalated)
    self.assertEqual(len(result.material_alerts), 1)

def test_description_only_change_is_not_material(self):
    self.orchestrator.process_webhook(self.alertmanager_payload)
    noisy = copy.deepcopy(self.alertmanager_payload)
    noisy["alerts"][0]["annotations"]["description"] = "Test description 91.4%"
    result = self.orchestrator.process_webhook(noisy)
    self.assertEqual(result.material_alerts, [])
```

Add `import copy` at the top if absent. Confirm the payload's severity and description live at
those exact keys before running — read `self.alertmanager_payload`'s definition in the test file
and adjust the paths if they differ.

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_services.py -k material -v
```

Expected: `AttributeError: 'ProcessingResult' object has no attribute 'material_alerts'`.

**Step 3: Add the field to `ProcessingResult`**

After the existing `alerts` field (`services.py:131`):

```python
    # Alerts whose update deserves its own downstream pipeline run — see
    # apps.alerts.materiality. Populated as alerts are written, because by the time
    # a caller sees this list the old severity/status/context_key are already gone.
    material_alerts: list[Alert] = field(default_factory=list)
```

**Step 4: Stamp the key and record materiality in `_create_alert`**

A newly created alert is always material. In `_create_alert`, compute the key before
`Alert.objects.create(...)` and pass it in:

```python
        from apps.alerts.context_keys import context_key_for

        alert = Alert.objects.create(
            ...
            node=resolve_node(parsed.labels),
            context_key=context_key_for(
                (parsed.labels or {}).get("checker", ""), parsed.annotations
            ),
        )
```

and after the `AlertHistory` row is written, append it:

```python
        result.material_alerts.append(alert)
```

**Step 5: Compare and record in `_update_alert`**

In `_update_alert`, beside the existing `old_status = alert.status` snapshot (`services.py:329`):

```python
        from apps.alerts.context_keys import context_key_for
        from apps.alerts.materiality import is_material_change

        old_severity = alert.severity
        old_key = alert.context_key
        new_key = context_key_for((parsed.labels or {}).get("checker", ""), parsed.annotations)
```

Set `alert.context_key = new_key` alongside the other field assignments, and make sure
`context_key` is included in whatever `save()` call this method makes (check whether it saves
with `update_fields` — if so, add `"context_key"`).

After the status handling, before returning:

```python
        if is_material_change(
            old_severity=old_severity,
            new_severity=parsed.severity,
            old_status=old_status,
            new_status=parsed.status,
            old_key=old_key,
            new_key=new_key,
        ):
            result.material_alerts.append(alert)
```

**Step 6: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_services.py -v
```

Expected: PASS, including the pre-existing tests.

**Step 7: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): record materially-changed alerts on the webhook ingest path"
```

---

## Task 5: Record materiality in `CheckAlertBridge` (checker path)

The same two changes, on the parallel path. This is the path `listening_ports` travels.

**Files:**
- Modify: `apps/alerts/check_integration.py` — `_create_alert` (~`:270`), `_update_alert` (`:309-345`)
- Test: `apps/alerts/_tests/test_check_integration.py`

**Step 1: Write the failing tests**

```python
def test_new_check_alert_is_material(self):
    result = self.bridge.process_results([self._warning_result()])
    self.assertEqual(len(result.material_alerts), 1)

def test_identical_check_repush_is_not_material(self):
    self.bridge.process_results([self._warning_result()])
    result = self.bridge.process_results([self._warning_result()])
    self.assertEqual(result.material_alerts, [])

def test_new_unexpected_port_at_same_severity_is_material(self):
    self.bridge.process_results([self._ports_result(unexpected=[22])])
    result = self.bridge.process_results([self._ports_result(unexpected=[22, 8080])])
    self.assertEqual(len(result.material_alerts), 1)

def test_same_ports_reordered_is_not_material(self):
    self.bridge.process_results([self._ports_result(unexpected=[22, 8080])])
    result = self.bridge.process_results([self._ports_result(unexpected=[8080, 22])])
    self.assertEqual(result.material_alerts, [])
```

Write `_warning_result()` and `_ports_result(unexpected)` as helpers returning `CheckResult`
instances. `_ports_result` must set `checker_name="listening_ports"` and
`metrics={"unexpected_ports": [...], "listening_count": 12, "allowlist": []}` so the annotations
the bridge builds (`check_integration.py:161-163`) carry the key's input. Read the existing test
file's setup first and reuse its `CheckResult` construction style.

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_check_integration.py -k material -v
```

**Step 3: Mirror Task 4's changes**

In the bridge's `_create_alert`: pass `context_key=context_key_for(...)` into the `Alert` create
and append the alert to `result.material_alerts`.

In the bridge's `_update_alert` (`:309-345`): it already snapshots `old_severity`. Add
`old_status = alert.status`, `old_key = alert.context_key`, compute `new_key`, assign
`alert.context_key = new_key`, add `"context_key"` to the existing `update_fields` list
(`:324-331`), and append to `result.material_alerts` when `is_material_change(...)` is true.

**Step 4: Run the tests**

```bash
uv run pytest apps/alerts/_tests/test_check_integration.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/check_integration.py apps/alerts/_tests/test_check_integration.py
git commit -m "feat(alerts): record materially-changed alerts on the checker ingest path"
```

---

## Task 6: Carry material incident ids through the stage DTOs

**Files:**
- Modify: `apps/orchestration/dtos.py` — `IngestResult`, `CheckResult`
- Modify: `apps/orchestration/executors.py` — `IngestExecutor.execute` (`:78-96`), `CheckExecutor.execute`
- Test: `apps/orchestration/_tests/test_executors.py`

**Step 1: Write the failing test**

```python
def test_ingest_result_carries_every_material_incident(self):
    # A payload with two firing alerts on different instances -> two incidents.
    ctx = self._ctx(payload={"driver": "alertmanager", "payload": two_instance_payload})
    result = IngestExecutor().execute(ctx)
    self.assertEqual(len(set(result.material_incident_ids)), 2)
```

Build `two_instance_payload` from the existing fixtures in that test module — two alerts with
different `instance` labels, both firing, both `severity: warning`.

**Step 2: Run and watch it fail**

```bash
uv run pytest apps/orchestration/_tests/test_executors.py -k material -v
```

**Step 3: Add the field to both DTOs**

```python
    #: Incidents that materially changed in this run and therefore each deserve their
    #: own downstream run. Deduplicated and ordered; see apps.alerts.materiality.
    material_incident_ids: list[int] = field(default_factory=list)
```

Confirm `to_dict()` includes it (if the DTO enumerates fields explicitly rather than using
`asdict`, add it there too) — the orchestrator reads it back from `output_snapshot` on resume.

**Step 4: Populate it in both executors**

In `IngestExecutor.execute`, after the existing subject selection block:

```python
            seen: set[int] = set()
            for alert in proc_result.material_alerts:
                incident_id = alert.incident_id
                if incident_id and incident_id not in seen:
                    seen.add(incident_id)
                    result.material_incident_ids.append(incident_id)
```

Add the identical block to `CheckExecutor.execute` against its own `ProcessingResult`. If the
two blocks are byte-identical, extract one helper — `apps/orchestration/routing.py` is where
shared subject rules already live.

**Step 5: Run the tests**

```bash
uv run pytest apps/orchestration/_tests/test_executors.py -v
```

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/orchestration/dtos.py apps/orchestration/executors.py apps/orchestration/_tests/test_executors.py
git commit -m "feat(orchestration): carry every material incident out of the entry stage"
```

---

## Task 7: Downstream runs execute a lane with no entry stage

A downstream run must run **exactly** its lane's stages. It must not treat ANALYZE as an entry
stage: a `resolved` incident routes to a lane listing only `notify`, and forcing an entry stage
would run the AI on an all-clear.

**Files:**
- Modify: `apps/orchestration/orchestrator.py` — `_execute_pipeline` (`:283`)
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing tests**

```python
def test_downstream_run_executes_only_its_lane_stages(self):
    incident = self._incident_with_alert(severity="critical")
    PipelineDefinition.objects.create(
        name="lane-notify-only", match=[], stages=["notify"], priority=10, is_active=True
    )
    run = PipelineRun.objects.create(
        trace_id="t-1", run_id="r-1", source="cluster",
        status=PipelineStatus.PENDING,
        inbound_payload={"downstream_incident_id": incident.id},
    )
    result = PipelineOrchestrator().execute_run(run)
    self.assertEqual(result.status, "SUCCESS")
    self.assertEqual(
        list(
            StageExecution.objects.filter(pipeline_run=run).values_list("stage", flat=True)
        ),
        ["notify"],
    )

def test_downstream_run_with_no_matching_lane_fails_no_route(self):
    incident = self._incident_with_alert(severity="critical")
    PipelineDefinition.objects.all().delete()
    run = PipelineRun.objects.create(
        trace_id="t-2", run_id="r-2", source="cluster",
        status=PipelineStatus.PENDING,
        inbound_payload={"downstream_incident_id": incident.id},
    )
    PipelineOrchestrator().execute_run(run)
    run.refresh_from_db()
    self.assertEqual(run.status, PipelineStatus.FAILED)
    self.assertIn("no_route", run.last_error_message)
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/orchestration/_tests/test_orchestrator.py -k downstream -v
```

Expected: the run executes INGEST against a payload with no `driver` and fails.

**Step 3: Branch on the marker in `_execute_pipeline`**

In the stage-selection block (`:325-352`), add a third branch **before** the `checks_only` check:

```python
        downstream_incident_id = payload.get("downstream_incident_id")
        if downstream_incident_id:
            # A downstream run has no entry stage: the incident was already ingested
            # by its parent. Route immediately from that incident's subject alert and
            # run exactly what the lane lists — which may be NOTIFY alone, for a
            # resolved incident whose lane skips the AI.
            active_stages = []
            entry_stage = None
            run_routes = False
            alert_id = self._legacy_subject_alert_id(downstream_incident_id)
            incident_id = downstream_incident_id
            active_stages.extend(self._downstream_or_fail(alert_id, pipeline_run.origin))
            final_status = self._final_status(active_stages, PipelineStage.INGEST)
```

Three details this branch must get right, each of which will break a test if missed:

1. `pipeline_run.mark_started(active_stages[0])` (`:363`) raises `IndexError` on an empty stage
   list. Guard it — a lane listing no stages is legal and must complete cleanly.
2. `_downstream_or_fail` raises `StageExecutionError` for `no_route`. It is currently called
   inside the `try` block; called here it sits outside, so either move this branch inside the
   existing `try` or wrap it so the run is still marked FAILED with `retryable=False`.
3. `incident_id` must be set **before** the loop so `NotifyExecutor._route_incident`
   (`executors.py:312`) can read the incident's stamped lane.

Also confirm `route_from_entry_stage`'s early return (`if stage != entry_stage ...`) is safe with
`entry_stage = None` — with `run_routes = False` it returns immediately, but read it and be sure.

**Step 4: Run the tests**

```bash
uv run pytest apps/orchestration/_tests/test_orchestrator.py -v
```

Expected: PASS, including every pre-existing orchestrator test.

**Step 5: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/_tests/test_orchestrator.py
git commit -m "feat(orchestration): execute a downstream run as its lane, with no entry stage"
```

---

## Task 8: The push run enqueues one downstream run per material incident

This is the behaviour change. The push run stops extending its own stage list and instead
records PENDING runs for the drain.

**Files:**
- Modify: `apps/orchestration/orchestrator.py` — `route_from_entry_stage` (`:369-397`)
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing tests**

```python
def test_push_with_three_material_incidents_enqueues_three_runs(self):
    result = PipelineOrchestrator().run_pipeline(
        payload={"driver": "cluster", "payload": three_checker_push}, source="cluster"
    )
    children = PipelineRun.objects.filter(trace_id=result.trace_id).exclude(
        run_id=result.run_id
    )
    self.assertEqual(children.count(), 3)
    self.assertEqual({c.status for c in children}, {PipelineStatus.PENDING})
    self.assertEqual(
        sorted(c.inbound_payload["downstream_incident_id"] for c in children),
        sorted(expected_incident_ids),
    )

def test_identical_repush_enqueues_nothing(self):
    orch = PipelineOrchestrator()
    orch.run_pipeline(payload={"driver": "cluster", "payload": three_checker_push}, source="cluster")
    before = PipelineRun.objects.count()
    orch.run_pipeline(payload={"driver": "cluster", "payload": three_checker_push}, source="cluster")
    # One new parent run, no new children.
    self.assertEqual(PipelineRun.objects.count(), before + 1)

def test_healthy_push_still_produces_its_parent_run(self):
    result = PipelineOrchestrator().run_pipeline(
        payload={"driver": "cluster", "payload": all_ok_push}, source="cluster"
    )
    self.assertTrue(PipelineRun.objects.filter(run_id=result.run_id).exists())

def test_children_inherit_trace_node_and_origin(self):
    result = PipelineOrchestrator().run_pipeline(
        payload={"driver": "cluster", "payload": three_checker_push}, source="cluster"
    )
    parent = PipelineRun.objects.get(run_id=result.run_id)
    child = PipelineRun.objects.filter(trace_id=parent.trace_id).exclude(run_id=parent.run_id).first()
    self.assertEqual(child.trace_id, parent.trace_id)
    self.assertNotEqual(child.run_id, parent.run_id)
    self.assertEqual(child.node_id, parent.node_id)
    self.assertEqual(child.origin, parent.origin)
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/orchestration/_tests/test_orchestrator.py -k enqueue -v
```

**Step 3: Replace the body of `route_from_entry_stage`**

It no longer extends `active_stages`; it enqueues. Keep the existing docstring's invariant
("only the entry stage routes") and the `routed` guard, and keep it running last for the entry
stage for the same reason the comment gives.

```python
        def route_from_entry_stage(stage: PipelineStage) -> None:
            nonlocal routed, final_status
            if stage != entry_stage or not run_routes or routed:
                return
            routed = True
            material = previous_results.get(stage, {}).get("material_incident_ids") or []
            self._enqueue_downstream_runs(pipeline_run, material)
            final_status = STAGE_TO_STATUS[entry_stage]
```

Then add the method:

```python
    def _enqueue_downstream_runs(
        self, parent: PipelineRun, incident_ids: list[int]
    ) -> list[PipelineRun]:
        """Record one PENDING run per materially-changed incident.

        Each child carries the parent's ``trace_id`` — so ``manage.py trace`` still
        shows one push as one story — with its own ``run_id``, and routes itself from
        its own incident. They are left PENDING for ``process_inbox`` rather than run
        inline: a node with eight incidents would otherwise make eight LLM calls
        inside one drain tick, which is the memory pressure Phase C exists to avoid.
        """
        children = []
        with transaction.atomic():
            for incident_id in incident_ids:
                children.append(
                    PipelineRun.objects.create(
                        trace_id=parent.trace_id,
                        run_id=str(uuid.uuid4()),
                        source=parent.source,
                        environment=parent.environment,
                        status=PipelineStatus.PENDING,
                        max_retries=self.max_retries,
                        inbound_payload={"downstream_incident_id": incident_id},
                        origin=parent.origin,
                        node=parent.node,
                        incident_id=incident_id,
                    )
                )
        logger.info(
            "Enqueued %d downstream run(s) for trace_id=%s",
            len(children),
            parent.trace_id,
            extra={"trace_id": parent.trace_id, "run_id": parent.run_id},
        )
        return children
```

Check whether `PipelineRun.incident_id` is a plain integer column or an FK before passing
`incident_id=` — `_execute_pipeline` assigns `pipeline_run.incident_id = incident_id` directly
(`:449`), so it is almost certainly fine, but confirm.

**Step 4: Delete what fan-out replaces**

`_downstream_or_fail` is no longer called from the push path — only from Task 7's downstream
branch. Confirm with `grep -rn "_downstream_or_fail" apps/` and leave it in place; it is now the
downstream run's routing entry point. `_final_status` keeps its remaining call site.

**Step 5: Run the full suite**

```bash
uv run pytest -q
```

Several existing tests will fail here, because a push run no longer notifies. That is the
intended behaviour change. For each failure decide deliberately: a test asserting "the webhook
run notified" becomes "the webhook run enqueued a downstream run that notifies once drained".
Do not weaken an assertion to make it pass — rewrite it against the new model.

**Step 6: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/_tests/
git commit -m "feat(orchestration): fan out one downstream run per materially-changed incident"
```

---

## Task 9: Synchronous entry points drain their own children

`run_pipeline()` is the synchronous entry point used by `manage.py run_pipeline --sample`, CLI
diagnostics and much of the test suite. After Task 8 it returns before anything is analysed or
notified, which silently breaks those workflows.

**Files:**
- Modify: `apps/orchestration/orchestrator.py` — `run_pipeline` (`:218`)
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing test**

```python
def test_run_pipeline_drains_its_own_children(self):
    result = PipelineOrchestrator().run_pipeline(
        payload={"driver": "cluster", "payload": three_checker_push}, source="cluster"
    )
    children = PipelineRun.objects.filter(trace_id=result.trace_id).exclude(run_id=result.run_id)
    self.assertEqual(children.count(), 3)
    self.assertEqual({c.status for c in children}, {PipelineStatus.SUCCESS})
```

Use whatever terminal status the codebase actually uses for a completed run — check
`PipelineStatus` and an existing success assertion before writing this.

**Step 2: Run and watch it fail**

Expected: children are still PENDING.

**Step 3: Drain at the end of `run_pipeline` only**

`run_pipeline` starts a run and executes it inline; `execute_run` (the drain entry point) must
**not** do this, or `process_inbox` would recurse into nested drains.

```python
        result = self._execute_pipeline(pipeline_run, payload)

        # Synchronous callers (manage.py run_pipeline, CLI diagnostics, tests) expect
        # one call to carry the whole pipeline through. Drain the children this run
        # enqueued, and only those: execute_run() deliberately does not, because
        # process_inbox is already the drain.
        from apps.orchestration.inbox import drain_run

        for child in PipelineRun.objects.filter(
            trace_id=pipeline_run.trace_id, status=PipelineStatus.PENDING
        ).exclude(run_id=pipeline_run.run_id):
            drain_run(child.run_id)

        return result
```

Read `drain_run`'s signature first (`apps/orchestration/inbox.py:77`) — confirm whether it takes
a `run_id` string or a pk, and match it.

**Step 4: Run the tests**

```bash
uv run pytest apps/orchestration/_tests/ -q && uv run python manage.py run_pipeline --sample --dry-run
```

Expected: PASS, and the sample run still shows analysis and notification.

**Step 5: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/_tests/test_orchestrator.py
git commit -m "feat(orchestration): synchronous runs drain the children they enqueue"
```

---

## Task 10: Seed the `resolved → notify` lane

An all-clear should reach the operator without an LLM call. That is a lane, not code.

**Files:**
- Create: `apps/orchestration/migrations/00XX_seed_resolved_lane.py`
- Test: `apps/orchestration/_tests/test_migrations.py` (or wherever `0012`'s seed is tested)

**Step 1: Write the failing test**

```python
def test_resolved_incidents_route_to_notify_only(self):
    lane = PipelineDefinition.objects.get(name="resolved-all-clear")
    self.assertEqual(lane.routable_stages(), ["notify"])
    self.assertTrue(lane.matches({"status": "resolved"}))
    self.assertFalse(lane.matches({"status": "firing"}))
```

Before writing this, confirm `facts_from_alert` (`apps/orchestration/routing.py:33`) actually
emits a `status` fact. **If it does not, this task's first job is adding it** — with its own test
— since the lane cannot match on a fact that is not produced.

**Step 2: Run and watch it fail**

```bash
uv run pytest apps/orchestration/_tests/ -k resolved -v
```

**Step 3: Write the migration**

Copy the structure of `0012_seed_default_lanes.py` exactly: module docstring explaining why the
row exists, `get_or_create` on `name` so an operator's existing row is never overwritten, and a
`backwards` that deletes only rows still matching the full seeded shape.

```python
_LANE = {
    "name": "resolved-all-clear",
    "description": "Resolved incidents notify without analysis: there is nothing left to "
    "diagnose, and an LLM call on an all-clear is pure cost.",
    "match": [{"field": "status", "op": "is", "value": "resolved"}],
    "stages": ["notify"],
    "priority": 40,
    "is_active": True,
}
```

Priority 40 puts it above `cluster-nodes` (50) so a resolved node alert takes it rather than the
node lane's `["analyze", "notify"]`. State that reasoning in the docstring the way `0012` states
its own.

**Step 4: Apply and test**

```bash
uv run python manage.py migrate && uv run pytest apps/orchestration/_tests/ -k resolved -v
```

**Step 5: Commit**

```bash
git add apps/orchestration/migrations apps/orchestration/_tests apps/orchestration/routing.py
git commit -m "feat(orchestration): seed a resolved lane that notifies without analysing"
```

---

## Task 11: End-to-end acceptance tests

The design's acceptance list, as one test module that exercises the real path.

**Files:**
- Create: `apps/orchestration/_tests/test_fanout_e2e.py`

**Step 1: Write the tests**

One test per acceptance criterion:

1. A push with three firing incidents produces three downstream runs resolving three lanes.
2. The same push repeated with nothing changed produces none.
3. A severity escalation on one incident produces exactly one downstream run.
4. A resolve produces one downstream run that notifies without analysing — assert there is **no**
   `analyze` `StageExecution` row.
5. A zero-incident push still produces its parent run.
6. A `listening_ports` alert gaining a new non-allowlisted port at unchanged severity produces a
   downstream run; the same port set repeated does not.
7. All children share the parent's `trace_id`, and `manage.py trace <trace_id>` shows them.

Drive them through `PipelineOrchestrator().run_pipeline(...)` so Task 9's drain runs, and assert
on `PipelineRun` and `StageExecution` rows rather than on mocks.

**Step 2: Run them**

```bash
uv run pytest apps/orchestration/_tests/test_fanout_e2e.py -v
```

**Step 3: Check coverage on everything changed**

```bash
uv run coverage run -m pytest && uv run coverage report
```

Every line and branch added by Tasks 1-10 must be covered.

**Step 4: Commit**

```bash
git add apps/orchestration/_tests/test_fanout_e2e.py
git commit -m "test(orchestration): end-to-end acceptance for incident fan-out"
```

---

## Task 12: Documentation

**Files:**
- Modify: `apps/orchestration/AGENTS.md` — the run model: one push run plus one downstream run
  per materially-changed incident, children sharing `trace_id`.
- Modify: `apps/alerts/AGENTS.md` — the gate, the context-key registry, and the rule that both
  ingest paths must record materiality through `is_material_change`.
- Modify: `docs/Architecture.md` — if it describes one run per push, correct it.
- Modify: `docs/plans/2026-08-19-incident-fanout-design.md` — **only** the two corrections named
  at the top of this plan (the context key is a hub-side registry, not a `BaseChecker` method;
  downstream runs have no entry stage). The design doc is not a historical record until it is
  merged, so correcting it now is right; older plans under `docs/plans/` are immutable.

**Verify:**

```bash
uv run pytest -q && uv run black . --check && uv run ruff check . && uv run pip-audit --strict --desc
```

**Commit:**

```bash
git add apps/orchestration/AGENTS.md apps/alerts/AGENTS.md docs/
git commit -m "docs: describe incident fan-out and the change gate"
```

---

## Deployment note

Hub-only. No node redeploy is required, because the context key is computed hub-side from
annotations that nodes already send. Order: migrate, then restart the hub, then confirm
`process_inbox` is draining — the inbox now carries downstream runs as well as inbound pushes,
so watch `INBOX_DEPTH_WARN` on the first few cycles.

{% endraw %}
