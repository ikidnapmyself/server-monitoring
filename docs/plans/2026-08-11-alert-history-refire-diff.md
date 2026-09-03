---
title: "2026-08-11 Alert History Re-fire Diff Implementation Plan"
parent: Plans
---

# Alert History Re-fire Diff Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record an `AlertHistory` row for every re-fire of an already-firing alert, capturing which fields changed, so a long-firing alert no longer shows only its single `"created"` row.

**Architecture:** Add a `_diff_alert(alert, parsed)` helper to `AlertOrchestrator` that snapshots meaningful field differences before the alert is overwritten. In `_update_alert`, compute the diff first, then: the status-unchanged `else` branch always writes an `event="updated"` history row with `details={"changed": diff}`; the existing status-change branch keeps its semantics but also attaches the same `details` diff.

**Tech Stack:** Django, pytest/pytest-django (`TestCase`), existing `AlertOrchestrator` in `apps/alerts/services.py`.

**Design doc:** `docs/plans/2026-08-11-alert-history-refire-diff-design.md`

**Branch:** `feat/alert-history-refire-diff` (already checked out; design doc already committed).

---

## Context the executor needs

- Source under change: `apps/alerts/services.py`.
  - `_process_alert` (`:208`) routes to `_create_alert` (new) or `_update_alert` (existing).
  - `_update_alert` (`:276-318`) currently overwrites fields at `:286-291`, and only writes an `AlertHistory` row when `parsed.status != old_status` (`:294-311`). The status-unchanged `else` branch (`:314-315`) writes no history.
- Model: `AlertHistory` (`apps/alerts/models.py:288`) has fields `alert`, `event`, `old_status`, `new_status`, `details` (JSON), `created_at` (auto).
- DTO: `ParsedAlert` (`apps/alerts/drivers/base.py:20`) has `severity`, `description`, `labels`, `annotations`, `status`, etc.
- Tests: `apps/alerts/_tests/test_services.py`. `AlertOrchestratorTests` (`:16`) drives the orchestrator via `self.orchestrator.process_webhook(self.alertmanager_payload)`. `self.alertmanager_payload` is an AlertManager dict with one firing alert (`fingerprint="test123"`, severity `warning`, description `"Test description"`). Sending it twice exercises the update path.
- Fields we diff: `severity`, `description`, `labels`, `annotations`. **Not** `raw_payload` (noisy) or `name` (fingerprint-stable). See design doc rationale table.
- Verify commands: `uv run pytest apps/alerts/_tests/test_services.py -v`, `uv run black .`, `uv run ruff check .`.

---

## Task 1: `_diff_alert` helper

**Files:**
- Modify: `apps/alerts/services.py` (add method to `AlertOrchestrator`)
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing test**

Add this class to `apps/alerts/_tests/test_services.py` (imports `AlertOrchestrator`, `Alert`, `AlertStatus` are already imported at the top of the file; add `from apps.alerts.drivers.base import ParsedAlert` if not present — it is already imported, confirm before adding):

```python
class DiffAlertTests(TestCase):
    """Tests for AlertOrchestrator._diff_alert."""

    def setUp(self):
        self.orchestrator = AlertOrchestrator()
        self.alert = Alert.objects.create(
            fingerprint="fp",
            source="alertmanager",
            name="A",
            severity="warning",
            status="firing",
            description="old desc",
            labels={"a": "1"},
            annotations={"x": "1"},
            started_at=timezone.now(),
        )

    def _parsed(self, **overrides):
        base = dict(
            fingerprint="fp",
            name="A",
            status="firing",
            started_at=timezone.now(),
            severity="warning",
            description="old desc",
            labels={"a": "1"},
            annotations={"x": "1"},
        )
        base.update(overrides)
        return ParsedAlert(**base)

    def test_no_changes_returns_empty(self):
        self.assertEqual(self.orchestrator._diff_alert(self.alert, self._parsed()), {})

    def test_severity_change_captured(self):
        diff = self.orchestrator._diff_alert(self.alert, self._parsed(severity="critical"))
        self.assertEqual(diff, {"severity": ["warning", "critical"]})

    def test_description_and_annotation_change_captured(self):
        diff = self.orchestrator._diff_alert(
            self.alert, self._parsed(description="new desc", annotations={"x": "2"})
        )
        self.assertEqual(
            diff,
            {"description": ["old desc", "new desc"], "annotations": [{"x": "1"}, {"x": "2"}]},
        )

    def test_raw_payload_and_name_not_diffed(self):
        diff = self.orchestrator._diff_alert(
            self.alert, self._parsed(name="B", raw_payload={"huge": "churn"})
        )
        self.assertEqual(diff, {})
```

Confirm `from django.utils import timezone` and `from apps.alerts.drivers.base import ParsedAlert` are imported at the top of the test file; add whichever is missing.

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_services.py::DiffAlertTests -v`
Expected: FAIL with `AttributeError: 'AlertOrchestrator' object has no attribute '_diff_alert'`

**Step 3: Write minimal implementation**

Add this method to the `AlertOrchestrator` class in `apps/alerts/services.py` (place it directly above `_update_alert`):

```python
# Fields compared on re-fire. raw_payload (noisy/large) and name
# (fingerprint-stable) are deliberately excluded — see design doc.
_DIFF_FIELDS = ("severity", "description", "labels", "annotations")

def _diff_alert(self, alert: Alert, parsed: ParsedAlert) -> dict:
    """Return {field: [old, new]} for meaningful fields that changed on re-fire."""
    diff: dict = {}
    for field_name in self._DIFF_FIELDS:
        old = getattr(alert, field_name)
        new = getattr(parsed, field_name)
        if old != new:
            diff[field_name] = [old, new]
    return diff
```

Ensure `ParsedAlert` is imported in `services.py` (check the existing imports; the module already references `ParsedAlert` — confirm the import exists, add `from apps.alerts.drivers.base import ParsedAlert` if it is only imported under `TYPE_CHECKING` or missing).

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_services.py::DiffAlertTests -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): add _diff_alert helper for re-fire change detection"
```

---

## Task 2: Write an `updated` history row on every re-fire

**Files:**
- Modify: `apps/alerts/services.py:276-318` (`_update_alert`)
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing test**

Add to `apps/alerts/_tests/test_services.py`, inside `AlertOrchestratorTests` (it already has `self.alertmanager_payload` in `setUp`):

```python
def test_refire_no_change_records_updated_row_with_empty_diff(self):
    self.orchestrator.process_webhook(self.alertmanager_payload)
    self.orchestrator.process_webhook(self.alertmanager_payload)

    rows = AlertHistory.objects.filter(event="updated")
    self.assertEqual(rows.count(), 1)
    row = rows.get()
    self.assertEqual(row.old_status, "firing")
    self.assertEqual(row.new_status, "firing")
    self.assertEqual(row.details, {"changed": {}})

def test_refire_with_severity_change_records_diff(self):
    self.orchestrator.process_webhook(self.alertmanager_payload)
    self.alertmanager_payload["alerts"][0]["labels"]["severity"] = "critical"
    self.orchestrator.process_webhook(self.alertmanager_payload)

    row = AlertHistory.objects.get(event="updated")
    self.assertEqual(row.details, {"changed": {"severity": ["warning", "critical"]}})

def test_continuous_firing_records_one_row_per_webhook(self):
    for _ in range(4):
        self.orchestrator.process_webhook(self.alertmanager_payload)
    # 1 created + 3 updated
    self.assertEqual(AlertHistory.objects.filter(event="created").count(), 1)
    self.assertEqual(AlertHistory.objects.filter(event="updated").count(), 3)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_services.py::AlertOrchestratorTests::test_refire_no_change_records_updated_row_with_empty_diff -v`
Expected: FAIL — `AlertHistory.objects.filter(event="updated").count()` is 0.

**Step 3: Write minimal implementation**

Rewrite `_update_alert` in `apps/alerts/services.py`. Compute the diff **before** overwriting fields, and always write a history row:

```python
def _update_alert(
    self,
    alert: Alert,
    parsed: ParsedAlert,
    result: ProcessingResult,
) -> Alert:
    """Update an existing alert with new data."""
    old_status = alert.status

    # Snapshot what changed BEFORE overwriting fields below.
    changed = self._diff_alert(alert, parsed)

    # Update fields
    alert.name = parsed.name
    alert.severity = parsed.severity
    alert.description = parsed.description
    alert.labels = parsed.labels
    alert.annotations = parsed.annotations
    alert.raw_payload = parsed.raw_payload

    # Handle status change
    if parsed.status != old_status:
        alert.status = parsed.status

        if parsed.status == "resolved":
            alert.ended_at = parsed.ended_at or timezone.now()
            result.alerts_resolved += 1
            event = "resolved"
        else:
            alert.ended_at = None
            event = "refired"

        AlertHistory.objects.create(
            alert=alert,
            event=event,
            old_status=old_status,
            new_status=parsed.status,
            details={"changed": changed},
        )

        logger.info(f"Alert {event}: {alert.name} ({alert.fingerprint})")
    else:
        result.alerts_updated += 1
        AlertHistory.objects.create(
            alert=alert,
            event="updated",
            old_status=old_status,
            new_status=alert.status,
            details={"changed": changed},
        )

    alert.save()
    return alert
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_services.py::AlertOrchestratorTests -v`
Expected: PASS, including the three new tests and the pre-existing `test_process_updates_existing_alert` / `test_process_resolves_alert` / `test_refired_alert`.

**Step 5: Commit**

```bash
git add apps/alerts/services.py apps/alerts/_tests/test_services.py
git commit -m "feat(alerts): record an AlertHistory row on every re-fire with a change diff"
```

---

## Task 3: Confirm status-change rows carry the diff, and full-suite green

**Files:**
- Test: `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing/confirming test**

Add to `AlertOrchestratorTests`:

```python
def test_refired_row_carries_diff(self):
    # fire -> resolve -> fire again with a severity bump
    self.orchestrator.process_webhook(self.alertmanager_payload)
    self.alertmanager_payload["alerts"][0]["status"] = "resolved"
    self.orchestrator.process_webhook(self.alertmanager_payload)
    self.alertmanager_payload["alerts"][0]["status"] = "firing"
    self.alertmanager_payload["alerts"][0]["labels"]["severity"] = "critical"
    self.orchestrator.process_webhook(self.alertmanager_payload)

    row = AlertHistory.objects.get(event="refired")
    self.assertIn("severity", row.details["changed"])
```

**Step 2: Run test to verify it passes**

Run: `uv run pytest apps/alerts/_tests/test_services.py::AlertOrchestratorTests::test_refired_row_carries_diff -v`
Expected: PASS (implementation from Task 2 already attaches `details`; this locks it in).

**Step 3: Run the full alerts suite + lint/format**

Run:
```bash
uv run pytest apps/alerts/_tests/test_services.py -v
uv run black apps/alerts/services.py apps/alerts/_tests/test_services.py
uv run ruff check apps/alerts/services.py apps/alerts/_tests/test_services.py
```
Expected: all tests PASS; black reports files unchanged/reformatted; ruff clean.

**Step 4: Run the broader suite to catch regressions**

Run: `uv run pytest apps/alerts/`
Expected: PASS. If any pre-existing test asserted "no history on plain update", update it to reflect the new `"updated"` row (none is expected based on current tests, but check).

**Step 5: Commit**

```bash
git add apps/alerts/_tests/test_services.py
git commit -m "test(alerts): lock in change diff on refired/resolved history rows"
```

---

## Acceptance criteria

- A plain re-fire (status unchanged) creates exactly one `AlertHistory` row with `event="updated"` and `details={"changed": {...}}`.
- The diff includes only `severity`, `description`, `labels`, `annotations`; never `raw_payload` or `name`.
- N continuous firings yield 1 `created` + (N-1) `updated` rows — the original admin symptom is resolved.
- `resolved` and `refired` rows also carry `details={"changed": ...}`.
- `uv run pytest apps/alerts/` is green; black and ruff clean on changed files.

## Out of scope

No retention/pruning of `AlertHistory` (accepted unbounded growth for noisy alerts — see design doc).
