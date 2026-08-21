---
title: "Alert Write-Path Unification — Implementation Plan"
parent: Plans
---

{% raw %}

# Alert write-path unification and incident reopen — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan
> task-by-task.

**Goal:** Delete `CheckAlertBridge`'s duplicate alert write methods so there is one write path,
then make a refire reopen its incident — in that order, so the reopen is written once instead of
twice.

**Architecture:** The bridge already converts a `CheckResult` into a `ParsedAlert`
(`check_result_to_parsed_alert`) and already holds a fully configured `AlertOrchestrator`
(`check_integration.py:118`). So `CheckAlertBridge._process_alert` becomes a shell holding the one
checker-specific rule and delegates everything else to `AlertOrchestrator._process_alert`. The
bridge's `_create_alert`, `_update_alert`, `_resolve_alert` and `_check_incident_resolution` are
deleted. `Incident.reopen()` then joins `resolve()`/`close()` and is called from the orchestrator's
single refire branch.

**Tech stack:** Django 5.2, pytest + pytest-django, `uv` for everything.

Read `docs/plans/2026-08-21-alert-write-path-unification-design.md` first.

---

## Context the executor needs

### Line references (verify before editing; they drift)

- `apps/alerts/check_integration.py` — `_process_alert` (`:238`), `_create_alert` (`:266`),
  `_update_alert` (`:319`), `_resolve_alert` (`:397`), `_check_incident_resolution` (`:425`),
  `run_checks_and_alert` aggregation loop (`:474+`).
- `apps/alerts/services.py` — `_process_alert` (~`:255`), `_create_alert` (`:263`),
  `_update_alert` (`:322`) with the refire branch at (`:360-379`), `_create_or_attach_incident`
  (`:403`), `_check_incident_resolution` (`:464`).
- `apps/alerts/models.py` — `Incident.resolve` (`:281`), `Incident.close` (`:290`),
  `IncidentStatus` (`:24`).

### Two traps

1. **`AlertStatus` vs the string `"firing"`.** `ParsedAlert.status` is a plain string; `Alert.status`
   is an `AlertStatus`. The existing code compares `parsed.status == "firing"` and
   `alert.status == AlertStatus.RESOLVED`. Keep that convention; do not "tidy" one into the other.
2. **`Alert.objects.create(...)` in tests needs `started_at`** — it is NOT NULL with no default.

### Verify after every task

```bash
uv run pytest apps/alerts/_tests/ apps/orchestration/_tests/ -q
uv run black . && uv run ruff check . && uv run python manage.py check
```

Conventions: absolute imports, line length 100, 100% branch coverage on changed code, commit after
every task.

---

## Task 1: The quiet re-push short-circuit in `AlertOrchestrator`

Do this first and alone: it changes the **webhook** path, so it should be one revertable commit
that nothing else is mixed into.

**Files:**
- Modify: `apps/alerts/services.py` — `_process_alert`
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing tests**

Add to `apps/alerts/_tests/test_services.py`, as a new class at the end of the file:

```python
class QuietRepushTests(TestCase):
    """An OK re-push of something already quiet must write nothing at all.

    Nodes push resolved results every tick (push_to_hub.py:112). Running those
    through _update_alert wrote an `updated` AlertHistory row each time — on the
    order of 30k rows a day across a healthy fleet, none of which says anything.
    """

    def setUp(self):
        self.orchestrator = AlertOrchestrator()
        self.payload = {
            "version": "4",
            "groupKey": "test",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "TestAlert", "severity": "warning"},
                    "annotations": {"description": "hot"},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "quiet-1",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def _resolved(self, description="cool"):
        payload = copy.deepcopy(self.payload)
        payload["alerts"][0]["status"] = "resolved"
        payload["alerts"][0]["annotations"]["description"] = description
        return payload

    def test_a_repeated_resolved_push_writes_no_history(self):
        self.orchestrator.process_webhook(self.payload)
        self.orchestrator.process_webhook(self._resolved())
        before = AlertHistory.objects.count()

        self.orchestrator.process_webhook(self._resolved(description="cool again"))

        self.assertEqual(AlertHistory.objects.count(), before)

    def test_a_repeated_resolved_push_does_not_touch_the_row(self):
        self.orchestrator.process_webhook(self.payload)
        self.orchestrator.process_webhook(self._resolved())
        alert = Alert.objects.get(fingerprint="quiet-1")
        stamp = alert.updated_at

        result = self.orchestrator.process_webhook(self._resolved(description="cool again"))

        alert.refresh_from_db()
        self.assertEqual(alert.updated_at, stamp)
        self.assertEqual(alert.description, "cool")  # not overwritten
        self.assertEqual(result.alerts_updated, 0)
        self.assertEqual(result.material_alerts, [])

    def test_the_first_resolve_still_records_normally(self):
        """The short-circuit must not swallow the transition itself."""
        self.orchestrator.process_webhook(self.payload)

        result = self.orchestrator.process_webhook(self._resolved())

        self.assertEqual(result.alerts_resolved, 1)
        self.assertTrue(AlertHistory.objects.filter(event="resolved").exists())
        self.assertEqual(len(result.material_alerts), 1)

    def test_a_refire_after_the_quiet_period_still_fires(self):
        """A firing push is never short-circuited."""
        self.orchestrator.process_webhook(self.payload)
        self.orchestrator.process_webhook(self._resolved())
        self.orchestrator.process_webhook(self._resolved())

        result = self.orchestrator.process_webhook(self.payload)

        alert = Alert.objects.get(fingerprint="quiet-1")
        self.assertEqual(alert.status, AlertStatus.FIRING)
        self.assertEqual(len(result.material_alerts), 1)
```

`copy`, `AlertHistory`, `AlertStatus` and `Alert` are already imported in that module — check
before adding imports.

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_services.py -k Quiet -v
```

Expected: the first two fail (a history row IS written, `alerts_updated` is 1); the last two pass
already.

**Step 3: Add the short-circuit**

In `AlertOrchestrator._process_alert`, between the `existing` lookup and the create/update
branch:

```python
        if existing and existing.status == AlertStatus.RESOLVED and parsed.status == "resolved":
            # Nothing to record: a re-push of something already quiet. Nodes push OK
            # results every tick (push_to_hub.py:112), and running those through
            # _update_alert wrote an `updated` AlertHistory row each time — ~30k rows
            # a day across a healthy fleet, none of which says anything. Never
            # material either, so no downstream run was ever involved. The FIRST
            # resolve is a status transition and does not come through here.
            return existing
```

**Step 4: Run the tests**

```bash
uv run pytest apps/alerts/_tests/ -q
```

Expected: PASS. If a pre-existing test asserted a history row for a repeated resolved push,
rewrite it against the new behaviour — do not weaken it.

**Step 5: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "perf(alerts): a re-push of an already-resolved alert writes nothing"
```

---

## Task 2: `CheckAlertBridge` delegates to the one write path

**Files:**
- Modify: `apps/alerts/check_integration.py` — `_process_alert`; delete `_create_alert`,
  `_update_alert`, `_resolve_alert`, `_check_incident_resolution`
- Test: `apps/alerts/_tests/test_check_integration.py`

**Step 1: Write the failing test for the rule that must survive**

```python
class CheckerSpecificGuardTests(TestCase):
    """The one rule delegation must NOT lose.

    An OK result for a fingerprint the hub has never alerted on is not news. The
    orchestrator creates a row for any unknown fingerprint whatever its status, so
    without this guard every healthy checker would open a resolved Alert row on its
    first run.
    """

    def setUp(self):
        self.bridge = CheckAlertBridge(auto_create_incidents=True, hostname="test-server")

    def test_an_ok_result_for_an_unknown_fingerprint_creates_nothing(self):
        result = self.bridge.process_check_result(
            CheckResult(
                status=CheckStatus.OK,
                message="fine",
                metrics={"cpu_percent": 5.0},
                checker_name="cpu",
            )
        )

        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(Incident.objects.count(), 0)
        self.assertEqual(result.alerts_created, 0)
        self.assertEqual(result.material_alerts, [])
```

**Step 2: Run it**

```bash
uv run pytest apps/alerts/_tests/test_check_integration.py -k unknown_fingerprint -v
```

Expected: PASS already (the current bridge has this rule). This test exists to *hold* the rule
across the deletion in step 3 — run it again after.

**Step 3: Replace `_process_alert` and delete the four methods**

Replace the whole body of `CheckAlertBridge._process_alert` (`:238-264`) with:

```python
    def _process_alert(
        self,
        parsed: ParsedAlert,
        source: str,
        result: ProcessingResult,
    ) -> Alert | None:
        """Delegate to the one alert write path, keeping one checker-specific rule.

        This bridge used to carry its own create/update/resolve implementations. They
        drifted from AlertOrchestrator's three separate times — an empty
        ``CheckAlertResult.alerts``, missing materiality on resolve, and a refire that
        never reopened the row and so notified an all-clear for a critical problem.
        Converting a CheckResult into a ParsedAlert is this class's actual job; writing
        alerts is not. See docs/plans/2026-08-21-alert-write-path-unification-design.md.

        The surviving rule: an OK result for a fingerprint the hub has never alerted on
        is not news. The orchestrator creates a row for any unknown fingerprint whatever
        its status, so without this every healthy checker would open a resolved Alert
        row on its first run.
        """
        if (
            parsed.status != "firing"
            and not Alert.objects.filter(fingerprint=parsed.fingerprint, source=source).exists()
        ):
            return None
        return self.orchestrator._process_alert(parsed, source, result)
```

Then **delete** `_create_alert`, `_update_alert`, `_resolve_alert` and
`_check_incident_resolution` from this class, and point the caller at the orchestrator's:

```python
            # Handle incident auto-resolution
            if self.orchestrator.auto_resolve_incidents:
                self.orchestrator._check_incident_resolution()
```

Remove imports that are now unused (`AlertHistory`, `context_key_for`, `is_material_change`,
`AlertSeverity`, `resolve_node`, `timezone` — check each with ruff rather than by eye).

**Step 4: Run the whole alerts suite and expect failures**

```bash
uv run pytest apps/alerts/_tests/ -q
```

Several bridge tests will fail. Each one is a deliberate decision, not a nuisance:

- Tests asserting `event="severity_changed"` → the unified path writes `updated` / `refired` /
  `resolved`. Rewrite the assertion to the new event; the *behaviour* being tested (a severity
  change is recorded) still holds.
- Tests asserting `alerts_updated` on a status change → the unified path counts only
  non-status-change updates.
- Tests calling `bridge._update_alert(...)` / `bridge._resolve_alert(...)` directly → drive them
  through `process_check_result` instead. A test that reaches for a private method it no longer
  has is a test coupled to the old shape.

Do **not** weaken an assertion to make it pass. If a test's intent no longer has a home, delete it
and say so in the commit message.

**Step 5: Re-run the guard test from step 1**

```bash
uv run pytest apps/alerts/_tests/test_check_integration.py -k unknown_fingerprint -v
```

Expected: still PASS. This is the point of the whole task.

**Step 6: Full suite**

```bash
uv run pytest -q
```

Expected: PASS. `apps/orchestration/_tests/test_fanout_e2e.py` is the load-bearing one — it drives
real node pushes end to end.

**Step 7: Commit**

```bash
git add apps/alerts/check_integration.py apps/alerts/_tests/test_check_integration.py
git commit -m "refactor(alerts): one alert write path, not two"
```

---

## Task 3: `Incident.reopen()`

**Files:**
- Modify: `apps/alerts/models.py` — beside `resolve()` (`:281`) and `close()` (`:290`)
- Test: `apps/alerts/_tests/test_models.py`

**Step 1: Write the failing tests**

```python
class IncidentReopenTests(TestCase):
    """reopen() is the inverse of resolve()/close(), for an alert that fired again."""

    def _incident(self, status):
        return Incident.objects.create(title="t", severity="critical", status=status)

    def test_reopen_from_resolved_clears_resolved_at(self):
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve(summary="done")

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.resolved_at)

    def test_reopen_from_closed_clears_closed_at(self):
        incident = self._incident(IncidentStatus.OPEN)
        incident.close()

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.closed_at)

    def test_reopen_keeps_the_summary(self):
        """The old summary is history, not something a reopen should erase."""
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve(summary="was fixed by restarting")

        incident.reopen()

        incident.refresh_from_db()
        self.assertEqual(incident.summary, "was fixed by restarting")

    def test_reopen_without_save_does_not_write(self):
        """Mirrors resolve()/close(): save=False lets a caller batch the write."""
        incident = self._incident(IncidentStatus.OPEN)
        incident.resolve()

        incident.reopen(save=False)

        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertEqual(
            Incident.objects.get(pk=incident.pk).status, IncidentStatus.RESOLVED
        )
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/test_models.py -k Reopen -v
```

Expected: `AttributeError: 'Incident' object has no attribute 'reopen'`.

**Step 3: Implement**

```python
    def reopen(self, save: bool = True):
        """Return a resolved or closed incident to OPEN because it fired again.

        Both end states reopen. An operator's close is a deliberate final word, but a
        thing firing again is new evidence — and leaving it closed puts a FIRING alert
        under a non-open incident, which is the mismatch this exists to remove.

        ``summary`` is deliberately kept: what the last resolution concluded is part of
        the incident's history, not something a reopen should erase.
        """
        self.status = IncidentStatus.OPEN
        self.resolved_at = None
        self.closed_at = None
        if save:
            self.save(update_fields=["status", "resolved_at", "closed_at", "updated_at"])
```

**Step 4: Run**

```bash
uv run pytest apps/alerts/_tests/test_models.py -k Reopen -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/models.py apps/alerts/_tests/test_models.py
git commit -m "feat(alerts): Incident.reopen() for an alert that fired again"
```

---

## Task 4: A refire reopens its incident

**Files:**
- Modify: `apps/alerts/services.py` — `_update_alert`, the refire branch (`:360-379`)
- Test: `apps/alerts/_tests/test_services.py`, `apps/alerts/_tests/test_check_integration.py`

**Step 1: Write the failing tests**

On the webhook path, in `test_services.py`:

```python
class RefireReopensIncidentTests(TestCase):
    """A firing alert must never sit under a non-open incident.

    The alert's status is a ROUTING FACT and the incident is what notify reports,
    so the two disagreeing is not cosmetic.
    """

    def setUp(self):
        self.orchestrator = AlertOrchestrator()
        self.payload = {
            "version": "4",
            "groupKey": "t",
            "receiver": "webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "Flapper", "severity": "critical"},
                    "annotations": {"description": "d"},
                    "startsAt": "2024-01-08T10:00:00Z",
                    "fingerprint": "flap-1",
                }
            ],
            "groupLabels": {},
            "commonLabels": {},
        }

    def _resolved(self):
        payload = copy.deepcopy(self.payload)
        payload["alerts"][0]["status"] = "resolved"
        return payload

    def _incident(self):
        return Alert.objects.get(fingerprint="flap-1").incident

    def test_a_refire_reopens_a_resolved_incident(self):
        self.orchestrator.process_webhook(self.payload)
        self.orchestrator.process_webhook(self._resolved())
        self.assertEqual(self._incident().status, IncidentStatus.RESOLVED)

        self.orchestrator.process_webhook(self.payload)

        incident = self._incident()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
        self.assertIsNone(incident.resolved_at)

    def test_a_refire_reopens_a_closed_incident(self):
        self.orchestrator.process_webhook(self.payload)
        self.orchestrator.process_webhook(self._resolved())
        incident = self._incident()
        incident.close()

        self.orchestrator.process_webhook(self.payload)

        self.assertEqual(self._incident().status, IncidentStatus.OPEN)

    def test_an_acknowledged_incident_is_left_alone(self):
        """ACKNOWLEDGED is not an end state — reopening would erase operator intent."""
        self.orchestrator.process_webhook(self.payload)
        incident = self._incident()
        incident.acknowledge()

        self.orchestrator.process_webhook(self.payload)

        self.assertEqual(self._incident().status, IncidentStatus.ACKNOWLEDGED)

    def test_an_alert_without_an_incident_does_not_raise(self):
        """--no-incidents runs have no incident to reopen."""
        orchestrator = AlertOrchestrator(auto_create_incidents=False)
        orchestrator.process_webhook(self.payload)
        orchestrator.process_webhook(self._resolved())

        result = orchestrator.process_webhook(self.payload)

        self.assertFalse(result.has_errors)
        self.assertIsNone(self._incident())
```

Note `Incident.acknowledge()` takes only `save` — the `acknowledged_by` argument belongs to
`IncidentManager.acknowledge`, which is a different call.

On the checker path, in `test_check_integration.py` — this is the test that proves the
unification paid off, because no line of the fix is written in the bridge:

```python
    def test_a_checker_refire_reopens_the_incident_too(self):
        """Written nowhere in the bridge: it comes free from the shared write path."""
        self.bridge.process_check_result(self._result(CheckStatus.CRITICAL))
        self.bridge.process_check_result(self._result(CheckStatus.OK))
        incident = Alert.objects.get().incident
        self.assertEqual(incident.status, IncidentStatus.RESOLVED)

        self.bridge.process_check_result(self._result(CheckStatus.CRITICAL))

        incident.refresh_from_db()
        self.assertEqual(incident.status, IncidentStatus.OPEN)
```

Add it to the existing `CheckAlertBridgeRefireTests` class, which already has `_result`.

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/alerts/_tests/ -k Reopen -v
```

**Step 3: Implement**

In `_update_alert`'s refire branch, after `event = "refired"`:

```python
            else:
                alert.ended_at = None
                event = "refired"
                # The incident must follow the alert. An alert row is reused per
                # fingerprint, so its FK still points at the incident that was
                # resolved — and _find_open_incident only considers OPEN/ACKNOWLEDGED,
                # so nothing else would ever revisit it. Left alone, a FIRING alert
                # sits under a RESOLVED incident: notify reports an incident marked
                # resolved, and the admin contradicts itself.
                incident = alert.incident
                if incident is not None and incident.status in (
                    IncidentStatus.RESOLVED,
                    IncidentStatus.CLOSED,
                ):
                    incident.reopen()
```

**Step 4: Run**

```bash
uv run pytest apps/alerts/_tests/ -q
```

Expected: PASS, including the checker-path test that has no bridge-side code behind it.

**Step 5: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/
git commit -m "fix(alerts): a refire reopens its incident on both ingest paths"
```

---

## Task 5: End-to-end acceptance

**Files:**
- Modify: `apps/orchestration/_tests/test_fanout_e2e.py`

**Step 1: Write the test**

Add to `FanOutAcceptanceTests`:

```python
    def test_a_refire_notifies_as_firing_not_as_an_all_clear(self):
        """The original bug, end to end.

        A refired alert used to stay RESOLVED in the database, so the downstream run
        routed on `status: resolved`, took the seeded resolved-all-clear lane, and
        delivered an all-clear for a CRITICAL problem.
        """
        self.push([checker_alert("cpu")])
        self.push([checker_alert("cpu", severity="info", status="resolved")])

        result = self.push([checker_alert("cpu")])

        child = self.children(result).get()
        assert self.stages_of(child) == [PipelineStage.ANALYZE, PipelineStage.NOTIFY]
        incident = Incident.objects.get(pk=child.incident_id)
        assert incident.pipeline.name == "e2e-firing"
        assert incident.status == "open"
```

**Step 2: Run**

```bash
uv run pytest apps/orchestration/_tests/test_fanout_e2e.py -v
```

Expected: PASS (Tasks 2-4 made it so). If it fails, the earlier tasks are incomplete — do not
adjust this test to match.

**Step 3: Check coverage on everything changed**

```bash
uv run coverage run -m pytest && uv run coverage report --include="apps/alerts/*,apps/orchestration/*" -m
```

Every line and branch added by Tasks 1-4 must be covered.

**Step 4: Commit**

```bash
git add apps/orchestration/_tests/test_fanout_e2e.py
git commit -m "test(orchestration): a refire notifies as firing, end to end"
```

---

## Task 6: Documentation

**Files:**
- Modify: `apps/alerts/AGENTS.md` — the write-path section: one path now, what the bridge does and
  does not do, and the surviving checker-specific rule.
- Modify: `docs/Architecture.md` — only if it describes two ingest paths.

State plainly that `CheckAlertBridge` converts and delegates, and that new alert-write behaviour
belongs in `AlertOrchestrator` and reaches both paths from there. Name the quiet-re-push
short-circuit, because it is the one place the pipeline deliberately records nothing.

**Verify:**

```bash
uv run pytest -q && uv run black . --check && uv run ruff check . && uv run pip-audit --strict --desc
```

**Commit:**

```bash
git add apps/alerts/AGENTS.md docs/
git commit -m "docs: one alert write path"
```

---

## Deployment note

Hub-only; no migration, no node redeploy. Two behaviour changes an operator may notice on the
first day: `AlertHistory` stops growing on quiet ticks, and checker-path history events are named
`updated`/`refired`/`resolved` instead of `severity_changed`. Neither affects notification.

{% endraw %}
