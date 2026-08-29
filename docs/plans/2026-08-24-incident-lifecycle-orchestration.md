---
title: "Incident Lifecycle Through the Inbox — Implementation Plan"
parent: Plans
---

{% raw %}

# Incident Lifecycle Through the Inbox — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Every incident transition — from a node or from a human — becomes one PENDING inbox run, incident status gates alert-driven runs, and notify says what the incident *is* at send time.

**Architecture:** Promote the orchestrator's private `_enqueue_downstream_runs` to a shared `inbox.enqueue_incident_runs`; call it from the alert write path (as today) and from `IncidentService` operator transitions (new). Move the "does the incident follow the alert, and does anyone hear about it" decision out of `AlertOrchestrator._update_alert` into one gate function that reads incident status. Add `status` to the downstream headline.

**Tech Stack:** Django 5.2, SQLite, pytest + pytest-django, `uv`.

**Design doc:** `docs/plans/2026-08-24-incident-lifecycle-orchestration-design.md`

**Branch:** `design/incident-lifecycle-orchestration` (checked out; design committed as `fed86c6`).

---

## Context the executor needs

- There is **one** alert write path: `CheckAlertBridge` delegates to `AlertOrchestrator`
  (`apps/alerts/check_integration.py:37,112`). Changes to `_update_alert` cover both webhook
  and checker traffic. Do not touch the bridge.
- `AlertOrchestrator._update_alert` (`apps/alerts/services.py:352-449`) currently: diffs, sets
  fields, on status change reopens/joins/creates the incident inline (`:385-415`), writes
  `AlertHistory`, saves, then appends to `result.material_alerts` if `is_material_change`.
  `material_alerts` → `routing.material_incident_ids` → `_enqueue_downstream_runs`
  (`apps/orchestration/orchestrator.py:462-481, 679-722`).
- `IncidentService` (`apps/alerts/services.py:548-608`) has `acknowledge` / `resolve` / `close`.
  Admin (`apps/alerts/admin.py:338-380`) calls the model methods directly, bypassing the service.
- `NotifyExecutor._headline_facts` (`apps/orchestration/executors.py:350-367`) already reads the
  incident for downstream runs; it lacks `status`. `derive_headline`
  (`apps/orchestration/formatters.py:84`) builds `[SEVERITY] title`.
- `severity_rank` (`apps/alerts/services.py:108`) orders severities.
- Existing test `RefireReopensIncidentTests.test_an_acknowledged_incident_is_left_alone`
  (`apps/alerts/_tests/test_services.py:1024`) asserts an equal-severity refire keeps ACK. That
  stays true; escalation is the new case.
- caplog can't see `apps.*` loggers (`propagate=False`) — assert on rows, not logs.

### Verify after every task

```bash
uv run pytest apps/alerts apps/orchestration -q
uv run black . --check && uv run ruff check .
```

Before the final commit: `uv run coverage run -m pytest && uv run coverage report` — 100% branch
on every changed file.

---

### Task 1: Shared `enqueue_incident_runs` in the inbox

**Files:**
- Modify: `apps/orchestration/inbox.py`
- Modify: `apps/orchestration/orchestrator.py:679-722`
- Test: `apps/orchestration/_tests/test_inbox.py`

**Step 1: Failing test**

```python
class EnqueueIncidentRunsTests(TestCase):
    def test_one_pending_run_per_incident_with_given_trace_and_origin(self):
        from apps.alerts.models import Incident
        from apps.orchestration import inbox
        from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus

        a = Incident.objects.create(title="a", severity="critical")
        b = Incident.objects.create(title="b", severity="warning")

        runs = inbox.enqueue_incident_runs(
            [a.id, b.id], trace_id="t-1", origin=PipelineOrigin.MANUAL, source="admin"
        )

        self.assertEqual(len(runs), 2)
        for run, inc in zip(runs, (a, b)):
            self.assertEqual(run.status, PipelineStatus.PENDING)
            self.assertEqual(run.trace_id, "t-1")
            self.assertEqual(run.origin, PipelineOrigin.MANUAL)
            self.assertEqual(run.incident_id, inc.id)
            self.assertEqual(run.inbound_payload, {"downstream_incident_id": inc.id})
        self.assertEqual(len({r.run_id for r in runs}), 2)
        self.assertEqual(PipelineRun.objects.count(), 2)

    def test_empty_list_enqueues_nothing(self):
        from apps.orchestration import inbox
        from apps.orchestration.models import PipelineOrigin, PipelineRun

        self.assertEqual(inbox.enqueue_incident_runs([], trace_id="t", origin=PipelineOrigin.MANUAL), [])
        self.assertEqual(PipelineRun.objects.count(), 0)
```

**Step 2:** `uv run pytest apps/orchestration/_tests/test_inbox.py -k EnqueueIncidentRuns -v` → FAIL, `AttributeError: enqueue_incident_runs`.

**Step 3: Implement** — add to `apps/orchestration/inbox.py` (imports: `uuid`, `transaction`, `PipelineOrigin`; keep the module's "single source of truth" docstring and extend it with one sentence: it is also where every downstream incident run is recorded).

```python
def enqueue_incident_runs(
    incident_ids,
    *,
    trace_id: str,
    origin: str,
    source: str = "",
    environment: str = "",
    node=None,
    max_retries: int = 3,
    parent_run_id: str = "",
) -> list[PipelineRun]:
    """Record one PENDING run per incident — the ONE way an incident change reaches on-call.

    Two producers call this: the alert write path (a node changed an incident) and
    ``IncidentService`` (a human did). Neither runs anything; ``drain`` is the only
    executor. Left PENDING rather than run inline for the reasons on
    ``PipelineOrchestrator._enqueue_downstream_runs``.
    """
    runs: list[PipelineRun] = []
    with transaction.atomic():
        for incident_id in incident_ids:
            runs.append(
                PipelineRun.objects.create(
                    trace_id=trace_id,
                    run_id=str(uuid.uuid4()),
                    source=source,
                    environment=environment,
                    status=PipelineStatus.PENDING,
                    max_retries=max_retries,
                    inbound_payload={"downstream_incident_id": incident_id},
                    origin=origin,
                    node=node,
                    incident_id=incident_id,
                )
            )
    if runs:
        logger.info(
            "Enqueued %d incident run(s) for trace_id=%s",
            len(runs),
            trace_id,
            extra={"trace_id": trace_id, "run_id": parent_run_id},
        )
    return runs
```

Add `import logging` / `logger = logging.getLogger(__name__)` if the module lacks one. Note: `inbox.py` imports `PipelineOrchestrator` at module level; `orchestrator.py` must import `enqueue_incident_runs` **inside** `_enqueue_downstream_runs` to avoid a cycle.

Replace the body of `PipelineOrchestrator._enqueue_downstream_runs` (keep its docstring) with:

```python
        from apps.orchestration.inbox import enqueue_incident_runs

        return enqueue_incident_runs(
            incident_ids,
            trace_id=parent.trace_id,
            origin=parent.origin,
            source=parent.source,
            environment=parent.environment,
            node=parent.node,
            max_retries=self.max_retries,
            parent_run_id=parent.run_id,
        )
```

**Step 4:** `uv run pytest apps/orchestration -q` → PASS (fan-out e2e must still pass unchanged).

**Step 5:** `git commit -am "refactor(orchestration): one enqueue for incident runs, shared via inbox"`

---

### Task 2: The incident gate

**Files:**
- Create: `apps/alerts/incident_gate.py`
- Test: `apps/alerts/_tests/test_incident_gate.py`

Pure function over an `Incident` + old/new alert facts. Returns `(reopen: bool, notify: bool)`. It does **not** save.

**Step 1: Failing tests** — one per table row in the design doc.

```python
from django.test import SimpleTestCase

from apps.alerts.incident_gate import follow_alert
from apps.alerts.models import Incident, IncidentStatus


def _inc(status):
    return Incident(status=status, severity="warning")


class FollowAlertTests(SimpleTestCase):
    def test_open_any_material_change_notifies_without_reopen(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.OPEN), "warning", "warning", "firing", "firing"), (False, True))

    def test_ack_escalation_reopens_and_notifies(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.ACKNOWLEDGED), "warning", "critical", "firing", "firing"), (True, True))

    def test_ack_refire_is_absorbed(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.ACKNOWLEDGED), "warning", "warning", "resolved", "firing"), (False, False))

    def test_ack_deescalation_is_absorbed(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.ACKNOWLEDGED), "critical", "warning", "firing", "firing"), (False, False))

    def test_resolved_refire_reopens_and_notifies(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.RESOLVED), "warning", "warning", "resolved", "firing"), (True, True))

    def test_closed_severity_change_while_firing_reopens_and_notifies(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.CLOSED), "warning", "critical", "firing", "firing"), (True, True))

    def test_resolved_alert_resolving_is_absorbed(self):
        self.assertEqual(follow_alert(_inc(IncidentStatus.RESOLVED), "warning", "warning", "firing", "resolved"), (False, False))

    def test_no_incident_notifies_nothing(self):
        self.assertEqual(follow_alert(None, "warning", "critical", "firing", "firing"), (False, False))
```

**Step 2:** run → FAIL, `ModuleNotFoundError`.

**Step 3: Implement** `apps/alerts/incident_gate.py`:

```python
"""Does the incident follow the alert, and does anyone hear about it?

One table, one place (design doc §2). ``is_material_change`` decides whether an
ALERT changed; this decides what that means for its INCIDENT. Both alert write
paths go through ``AlertOrchestrator._update_alert``, which is the only caller.
"""

from apps.alerts.models import IncidentStatus


def follow_alert(
    incident,
    old_severity: str,
    new_severity: str,
    old_status: str,
    new_status: str,
) -> tuple[bool, bool]:
    """Return ``(reopen, notify)`` for a material alert change under ``incident``.

    - OPEN: notify, nothing to reopen.
    - ACKNOWLEDGED: only an escalation breaks the ack; refires and de-escalations
      are absorbed (history row only).
    - RESOLVED / CLOSED: a firing alert reopens and notifies, whether it refired
      or merely changed severity. An alert going quiet is absorbed.
    """
    from apps.alerts.services import severity_rank

    if incident is None:
        return False, False
    firing = new_status == "firing"
    if incident.status == IncidentStatus.OPEN:
        return False, True
    if incident.status == IncidentStatus.ACKNOWLEDGED:
        escalated = severity_rank(new_severity) > severity_rank(old_severity)
        return (True, True) if escalated else (False, False)
    # RESOLVED / CLOSED
    return (True, True) if firing else (False, False)
```

**Step 4:** PASS. **Step 5:** `git add -A && git commit -m "feat(alerts): incident gate — one table for reopen/notify"`

---

### Task 3: Wire the gate into `_update_alert`

**Files:**
- Modify: `apps/alerts/services.py:352-449`
- Test: `apps/alerts/_tests/test_services.py` (extend `RefireReopensIncidentTests`)

**Step 1: Failing tests** — append to `RefireReopensIncidentTests`:

```python
    def _with_severity(self, severity):
        payload = copy.deepcopy(self.payload)
        payload["alerts"][0]["labels"]["severity"] = severity
        return payload

    def test_severity_change_on_a_resolved_incident_reopens_and_is_material(self):
        self.orchestrator.process_webhook(self._with_severity("warning"))
        self.orchestrator.process_webhook(self._resolved())
        # Still resolved as an alert? No — it must be FIRING with a new severity
        # while the incident is RESOLVED. Refire at the old severity first would
        # hit the refire branch; go straight from resolved to a different severity.
        result = self.orchestrator.process_webhook(self._with_severity("critical"))
        self.assertEqual(self._incident().status, IncidentStatus.OPEN)
        self.assertEqual([a.fingerprint for a in result.material_alerts], ["flap-1"])

    def test_escalation_breaks_an_ack(self):
        self.orchestrator.process_webhook(self._with_severity("warning"))
        self._incident().acknowledge()
        result = self.orchestrator.process_webhook(self._with_severity("critical"))
        self.assertEqual(self._incident().status, IncidentStatus.OPEN)
        self.assertEqual(len(result.material_alerts), 1)

    def test_equal_severity_refire_under_ack_is_absorbed(self):
        self.orchestrator.process_webhook(self.payload)
        self._incident().acknowledge()
        self.orchestrator.process_webhook(self._resolved())
        result = self.orchestrator.process_webhook(self.payload)
        self.assertEqual(self._incident().status, IncidentStatus.ACKNOWLEDGED)
        self.assertEqual(result.material_alerts, [])
        self.assertTrue(AlertHistory.objects.filter(alert__fingerprint="flap-1", event="refired").exists())
```

(Note: the `_resolved()` push under ACK is itself a status change → resolved; the gate absorbs it for ACK? No — the table says ACK + de-escalation/refire absorbs; an alert *resolving* under ACK is a status change firing→resolved at equal severity → absorbed. Assert that too if coverage needs it.)

**Step 2:** run → FAIL (first test: incident still RESOLVED; third: material_alerts non-empty).

**Step 3: Implement.** In `_update_alert`:

1. Keep the status-change block for **history event naming and incident attachment**, but remove the `incident.reopen()` call from it (keep sibling-join and create-if-none, which are about *which* incident, not *whether it reopens*).
2. After `alert.save()`, replace the final `if is_material_change(...)` with:

```python
        if is_material_change(
            old_severity=old_severity,
            new_severity=parsed.severity,
            old_status=old_status,
            new_status=parsed.status,
            old_key=old_key,
            new_key=new_key,
        ):
            reopen, notify = follow_alert(
                alert.incident, old_severity, parsed.severity, old_status, parsed.status
            )
            if reopen:
                alert.incident.reopen()
            if notify:
                result.material_alerts.append(alert)
        return alert
```

Import `from apps.alerts.incident_gate import follow_alert` inside the method (gate imports `services` for `severity_rank`).

Careful: a `context_key`-only change under OPEN must still notify (gate returns `(False, True)` for OPEN regardless of severity/status — it does). Update the long comment at `:385-401` to point at `incident_gate.py` instead of describing reopen inline.

**Step 4:** `uv run pytest apps/alerts apps/orchestration -q` → PASS, including `test_an_acknowledged_incident_is_left_alone` and `test_fanout_e2e`.

**Step 5:** `git commit -am "feat(alerts): incident status gates alert-driven runs"`

---

### Task 4: Operator transitions enqueue a run

**Files:**
- Modify: `apps/alerts/services.py:548-608` (`IncidentService`)
- Modify: `apps/alerts/admin.py:338-380` (actions call the service)
- Test: `apps/alerts/_tests/test_services.py` (`IncidentManagerTests`), `apps/alerts/_tests/test_admin.py`

**Step 1: Failing tests**

In `IncidentManagerTests`:

```python
    def test_resolve_enqueues_one_manual_pending_run(self):
        from apps.orchestration.models import PipelineOrigin, PipelineRun, PipelineStatus

        incident = Incident.objects.create(title="x", severity="critical")
        IncidentService.resolve(incident.id, resolved_by="ops")
        run = PipelineRun.objects.get()
        self.assertEqual(run.status, PipelineStatus.PENDING)
        self.assertEqual(run.origin, PipelineOrigin.MANUAL)
        self.assertEqual(run.incident_id, incident.id)
        self.assertEqual(run.inbound_payload, {"downstream_incident_id": incident.id})
        self.assertEqual(StageExecution.objects.count(), 0)  # nothing ran in-request

    def test_acknowledge_and_close_enqueue_too(self):
        from apps.orchestration.models import PipelineRun

        incident = Incident.objects.create(title="x", severity="critical")
        IncidentService.acknowledge(incident.id)
        IncidentService.close(incident.id)
        self.assertEqual(PipelineRun.objects.count(), 2)
```

In `test_admin.py::TestPerObjectActions` add to each existing button test one assertion:
`assert PipelineRun.objects.filter(incident=incident, status=PipelineStatus.PENDING, origin=PipelineOrigin.MANUAL).count() == 1`,
and to `test_resolve_selected_incidents` assert one run per resolved incident.

**Step 2:** run → FAIL (no runs).

**Step 3: Implement.** In `IncidentService` add:

```python
    @staticmethod
    def _announce(incident: Incident) -> None:
        """A human changed the incident: one inbox run, same as when a node does."""
        from apps.orchestration.inbox import enqueue_incident_runs
        from apps.orchestration.models import PipelineOrigin

        subject = incident.alerts.order_by("-received_at").first()
        enqueue_incident_runs(
            [incident.id],
            trace_id=str(uuid.uuid4()),
            origin=PipelineOrigin.MANUAL,
            source=subject.source if subject else "",
            node=subject.node if subject else None,
        )
```

Call `IncidentService._announce(incident)` at the end of `acknowledge`, `resolve`, `close` (before `return`). Add `import uuid` at top of `services.py`.

In `admin.py`, replace direct model calls with the service, keeping the status guards:

- `acknowledge_selected`: `IncidentService.acknowledge(incident.id, acknowledged_by=request.user.get_username())`
- `resolve_selected` / `resolve_incident`: `IncidentService.resolve(obj.id, resolved_by=request.user.get_username())`
- `acknowledge_incident`: `IncidentService.acknowledge(obj.id, acknowledged_by=...)`
- `close_incident`: `IncidentService.close(obj.id)`

Import: `from apps.alerts.services import IncidentService, instance_key_from_labels`. Keep the `message_user` texts unchanged (tests assert on redirects, not text, but don't invite churn).

**Step 4:** PASS. **Step 5:** `git commit -am "feat(alerts): operator transitions enqueue an inbox run"`

---

### Task 5: The headline says what the incident is

**Files:**
- Modify: `apps/orchestration/formatters.py:84-115`
- Modify: `apps/orchestration/executors.py:350-367`
- Test: `apps/orchestration/_tests/test_formatters.py`, `apps/orchestration/_tests/test_executors.py`

**Step 1: Failing tests**

```python
def test_derive_headline_prefixes_resolved_status():
    title, severity, _ = derive_headline(
        {"severity": "critical", "incident_title": "Disk full", "status": "resolved"}, {}
    )
    assert title == "[RESOLVED] Disk full"
    assert severity == "critical"


def test_derive_headline_prefixes_acknowledged_status():
    title, _, _ = derive_headline({"severity": "warning", "incident_title": "T", "status": "acknowledged"}, {})
    assert title == "[ACKNOWLEDGED] T"


def test_derive_headline_open_keeps_severity_prefix():
    title, _, _ = derive_headline({"severity": "warning", "incident_title": "T", "status": "open"}, {})
    assert title == "[WARNING] T"
```

And in `test_executors.py`, a test that `NotifyExecutor._headline_facts(incident)` includes `"status": incident.status`.

**Step 2:** FAIL. **Step 3: Implement.**

`formatters.derive_headline`, after computing `severity`:

```python
    status = (ingest.get("status") or "open").lower()
    prefix = status.upper() if status in ("acknowledged", "resolved", "closed") else severity.upper()
    if incident_title:
        title = f"[{prefix}] {incident_title}"
    else:
        title = f"[{prefix}] {source}: incident"
```

`executors._headline_facts`: add `"status": incident.status,`. Ingest snapshots for push runs carry no `status`, so their headline is unchanged (`open` default).

**Step 4:** PASS. **Step 5:** `git commit -am "feat(notify): headline reflects live incident status"`

---

### Task 6: End-to-end + docs

**Files:**
- Test: `apps/orchestration/_tests/test_fanout_e2e.py` (append)
- Modify: `apps/alerts/AGENTS.md`, `apps/orchestration/AGENTS.md`

**Step 1: Failing e2e test** (reuse the file's `_stub_outbound` and lane seeding; drain via `call_command("process_inbox")`):

```python
    def test_operator_resolve_reaches_notify_through_the_inbox(self):
        # 1. a push opens an incident and its downstream run notifies (existing path)
        # 2. IncidentService.resolve(incident.id)
        # 3. exactly one new PENDING run, origin=manual; no StageExecution for it yet
        # 4. call_command("process_inbox"); that run now has a NOTIFY StageExecution
        #    whose output_snapshot / captured message title starts with "[RESOLVED]"
```

Write it against rows as the file's docstring insists.

**Step 2:** FAIL until drained title check → then PASS after Tasks 1-5 (if it passes immediately, good — keep it).

**Step 3: Docs.** `apps/alerts/AGENTS.md`: add a short "Incident gate" section pointing at `incident_gate.py` and the table. `apps/orchestration/AGENTS.md`: under the inbox, state that `enqueue_incident_runs` is the single producer entry and that `IncidentService` is its second caller. `docs/Architecture.md`: one sentence if it describes fan-out.

**Step 4:** Full verify:

```bash
uv run pytest -q
uv run coverage run -m pytest && uv run coverage report | grep -E "incident_gate|inbox|services|formatters|executors|admin"
uv run black . --check && uv run ruff check .
```

**Step 5:** `git commit -am "test+docs: operator resolve end to end through the inbox"`, then open a PR from `design/incident-lifecycle-orchestration` (never push main).

{% endraw %}
