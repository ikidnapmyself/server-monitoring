---
title: "Phase A: Pipeline Routing Spine — Implementation Plan"
parent: Plans
---

# Phase A: Pipeline Routing Spine

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task.

**Goal:** Introduce the routing spine — `PipelineDefinition` gains match/priority/flags/channels, a first-match-wins resolver picks the pipeline for an incident, and `NotifyExecutor` sends to the matched pipeline's channels (falling back to today's "first active"). **Non-breaking:** additive fields + a fallback; the legacy graph `config` and `DefinitionBasedOrchestrator` are untouched (retired in Phase D).

**Design:** `docs/plans/2026-08-01-pipeline-routing-north-star-design.md`.

**Tech Stack:** Django 5.2, pytest, uv. **Conventions:** absolute imports; 100% branch coverage on changed lines; TDD; commit per task.

---

## Task 1: Routing fields on `PipelineDefinition` + `matches()`

**Files:** Modify `apps/orchestration/models.py`; test `apps/orchestration/_tests/test_pipeline_routing.py` (create).

Add to `PipelineDefinition`:
```python
    match = models.JSONField(default=list, blank=True,
        help_text="Routing conditions [{field, op, value}]; empty = catch-all.")
    priority = models.IntegerField(default=100, db_index=True,
        help_text="Lower is evaluated first (first match wins).")
    run_checkers = models.BooleanField(default=True)
    run_intelligence = models.BooleanField(default=True)
    run_notify = models.BooleanField(default=True)
    channels = models.ManyToManyField("notify.NotificationChannel", blank=True, related_name="pipelines")
```
Method (ops: `is`, `is-not`, `in`, `not-in`; fields: `source`, `severity`, `instance`, `label:<k>`):
```python
    def matches(self, facts: dict) -> bool:
        for cond in self.match or []:
            field, op, value = cond.get("field"), cond.get("op", "is"), cond.get("value")
            actual = self._fact(facts, field)
            if op == "is" and actual != value:
                return False
            if op == "is-not" and actual == value:
                return False
            if op == "in" and actual not in (value or []):
                return False
            if op == "not-in" and actual in (value or []):
                return False
        return True

    @staticmethod
    def _fact(facts, field):
        if field and field.startswith("label:"):
            return (facts.get("labels") or {}).get(field.split(":", 1)[1])
        return facts.get(field)
```
**Tests:** empty match ⇒ True (catch-all); `is`/`is-not`/`in`/`not-in`; `label:` lookup; multiple conditions AND. `makemigrations orchestration`; run tests.
**Commit:** `feat(orchestration): routing fields + matches() on PipelineDefinition`.

---

## Task 2: Resolver + fact extraction

**Files:** Create `apps/orchestration/routing.py`; test same module.

```python
def facts_from_incident(incident) -> dict:
    from apps.alerts.models import Alert
    labels: dict = {}
    source = ""
    for a in Alert.objects.filter(incident=incident):
        labels.update(a.labels or {})
        source = source or a.source
    return {
        "source": source,
        "severity": getattr(incident, "severity", ""),
        "instance": labels.get("instance_id", ""),
        "labels": labels,
    }


def resolve_pipeline(facts: dict):
    """First active pipeline (by priority, then id) whose match() passes, else None."""
    from apps.orchestration.models import PipelineDefinition
    for p in PipelineDefinition.objects.filter(is_active=True).order_by("priority", "id"):
        if p.matches(facts):
            return p
    return None
```
**Tests:** priority order / first-match-wins; negation short-circuit via a higher-priority rule; no match ⇒ None; catch-all (empty match, lowest priority) wins when nothing else does; `facts_from_incident` pulls source/labels/instance from alerts.
**Commit:** `feat(orchestration): pipeline resolver (first-match-wins)`.

---

## Task 3: Stamp matched pipeline on Incident; NotifyExecutor uses its channels

**Files:** `apps/alerts/models.py` (`Incident.pipeline` FK); `apps/orchestration/executors.py` (`NotifyExecutor`); tests in the executor test module.

- `Incident.pipeline = models.ForeignKey("orchestration.PipelineDefinition", null=True, blank=True, on_delete=models.SET_NULL, related_name="incidents")`. Migration.
- In `NotifyExecutor.execute`, before channel selection: build facts from `previous["ingest"]` (+ `ctx.incident_id`), `resolve_pipeline(facts)`, stamp `Incident.pipeline` if found, and **if the matched pipeline has active channels, send to each of those** (loop) instead of `NotifySelector`. If no pipeline or no channels ⇒ keep today's `NotifySelector` path (fallback). Keep it minimal — reuse the existing message build; only the channel set changes.
**Tests:** matched pipeline with a channel ⇒ notify targets that channel + `Incident.pipeline` stamped; no matching pipeline ⇒ falls back to first-active (existing behavior unchanged); pipeline with multiple channels ⇒ fan-out.
**Commit:** `feat(notify): route notifications via the matched pipeline's channels`.

---

## Task 4: Guided setup writes a `Pipeline`, not a bare channel

**Files:** `config/management/commands/setup_cluster.py` (`_ensure_notification_channel`); its test.

Change the hub notify setup so, after creating/finding the `NotificationChannel`, it also ensures a **catch-all `PipelineDefinition`** (`match=[]`, `priority=1000`, `run_*=True`) with that channel attached — so the channel is wired *through routing*, superseding the bare-channel interim (#181). If a catch-all already exists, attach the channel to it.
**Tests:** hub setup creates a catch-all pipeline bound to the channel; re-run doesn't duplicate.
**Commit:** `feat(cluster): guided hub wires the channel via a catch-all pipeline`.

---

## Task 5: Verify + docs

```bash
uv run black . --check && uv run ruff check . && uv run pytest -q
uv run coverage run -m pytest && uv run coverage report   # 100% on changed lines
```
Add an admin display for the new `PipelineDefinition` routing fields (match/priority/flags/channels) so operators can see/edit routes. Short docs note (Deployment or a Routing page): routes = match → flags → channels, first-match-wins, empty match = catch-all, no match = inbox (Phase C).
Finish via `@superpowers:finishing-a-development-branch`.

---

## Notes for the executor
- **Non-breaking:** no match ⇒ NotifyExecutor falls back to today's behavior; stage selection is unchanged (Phase B moves it to the flags). Do not touch `DefinitionBasedOrchestrator`.
- **Facts** come from the incident's alerts (source/labels/instance) + incident severity.
- **Coverage:** the four match ops, empty-match catch-all, resolver first-match vs none, and the notify matched-vs-fallback branches are the easy misses.
