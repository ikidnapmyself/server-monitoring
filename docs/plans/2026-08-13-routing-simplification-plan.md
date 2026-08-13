---
title: "Routing Simplification — Implementation Plan"
parent: Plans
---

# Routing Simplification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move every routing decision out of Python defaults and driver flags into
`PipelineDefinition` rows, so the path an alert takes is readable from the table.

**Architecture:** A lane is one row: `match` conditions in, an ordered `stages` list and one
`channel` out. The engine gains a single rule — *the entry stage produces an alert, the lane is
resolved from that alert, the lane's stages run* — applied identically to webhook ingest
(entry = INGEST) and hub checks (entry = CHECK). The implicit fallback in `_downstream_stages`,
the `skip_checkers` driver flag, and the three `run_*` booleans all go away.

**Tech Stack:** Django 5, pytest + pytest-django, `uv` for all commands, Black/Ruff at 100 cols.

**Design doc:** `docs/plans/2026-08-12-routing-simplification-design.md` — read it first. Section
numbers below (3.1, 7.2, …) refer to it.

**Branch:** work continues on `design/routing-simplification` (already checked out). Do not use
worktrees.

---

## Conventions for every task

- Absolute imports only (`from apps.alerts.models import Alert`).
- Run the full suite before each commit: `uv run pytest`.
- Format and lint before each commit: `uv run black . && uv run ruff check . --fix`.
- 100% branch coverage on changed lines. Check with
  `uv run coverage run -m pytest && uv run coverage report`.
- Commit after every task. Never push to `main`.

---

## Task order and why

Tasks 1–2 are independent and land first. Tasks 3–5 change the data model. Tasks 6–8 change
behaviour and depend on the model being in place. Task 9 is docs.

| # | Task | Fixes |
|---|---|---|
| 1 | Readable host column in alerts admin | 3.11 |
| 2 | Ingest selects its subject from its own alerts | 3.10(2) |
| 3 | `stages` field replaces the three `run_*` booleans | 3.2 (part) |
| 4 | `channel` FK replaces the `channels` M2M | 3.3 |
| 5 | Route on one alert, with `origin` as a fact | 3.4 |
| 6 | Delete the implicit fallback; seed the lanes | 3.1 |
| 7 | Delete `skip_checkers` | 3.2 |
| 8 | Route checker-generated runs | 3.9 |
| 9 | Docs | — |

**Deferred out of this plan:** routing `reeval_existing` through orchestration (3.8). It is the
most contentious behaviour change in the design — threshold edits would start producing outbound
messages — and it is separable from everything above. See "Deferred: re-eval through
orchestration" at the end of this plan.

---

## Task 1: Readable host column in the alerts admin

The `node` column is blank for every non-cluster source, because `resolve_node` needs an
`instance_id` label *and* a registered `Node`. `instance_key_from_labels` already falls through
`instance_id` → `instance` → `hostname` and is what defines "which host" for incident grouping,
so reuse it rather than inventing a second notion.

**Files:**
- Modify: `apps/alerts/admin.py:84-93` (`AlertAdmin.list_display`)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing test**

Add to `apps/alerts/_tests/test_admin.py`:

```python
class AlertAdminHostColumnTests(TestCase):
    def _admin(self):
        from django.contrib.admin.sites import AdminSite

        from apps.alerts.admin import AlertAdmin
        from apps.alerts.models import Alert

        return AlertAdmin(Alert, AdminSite())

    def test_host_prefers_registered_node(self):
        from apps.alerts.models import Alert, Node

        node = Node.objects.create(instance_id="node-a", hostname="web-01")
        alert = Alert.objects.create(
            name="cpu", fingerprint="fp1", source="cluster",
            labels={"instance_id": "node-a"}, node=node,
        )
        self.assertEqual(self._admin().host(alert), "node-a (web-01)")

    def test_host_falls_back_to_labels_for_webhook_sources(self):
        from apps.alerts.models import Alert

        alert = Alert.objects.create(
            name="cpu", fingerprint="fp2", source="grafana",
            labels={"instance": "10.0.0.7"},
        )
        self.assertEqual(self._admin().host(alert), "10.0.0.7")

    def test_host_is_dash_when_nothing_identifies_the_machine(self):
        from apps.alerts.models import Alert

        alert = Alert.objects.create(name="cpu", fingerprint="fp3", source="grafana", labels={})
        self.assertEqual(self._admin().host(alert), "—")
```

**Step 2: Run the tests to verify they fail**

```bash
uv run pytest apps/alerts/_tests/test_admin.py::AlertAdminHostColumnTests -v
```

Expected: FAIL — `AlertAdmin` has no attribute `host`.

**Step 3: Implement**

In `apps/alerts/admin.py`, replace `"node"` in `AlertAdmin.list_display` with `"host"` and add the
method to the class:

```python
    @admin.display(description="Host", ordering="node__instance_id")
    def host(self, obj):
        """Machine this alert concerns, for every source — not just cluster pushes.

        ``node`` is only linked for registered cluster nodes, so fall through to the
        shared label lookup that also defines incident grouping.
        """
        if obj.node_id:
            return str(obj.node)
        return instance_key_from_labels(obj.labels) or "—"
```

Add the import at the top of `apps/alerts/admin.py`:

```python
from apps.alerts.services import instance_key_from_labels
```

**Step 4: Run tests**

```bash
uv run pytest apps/alerts/_tests/test_admin.py -v
uv run pytest
```

Expected: PASS. Some existing admin tests may assert `"node" in list_display` — update them to
`"host"`.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "fix(alerts): show host for every source in the alerts admin"
```

---

## Task 2: Ingest selects its subject from its own alerts

`IngestExecutor` currently picks the routed subject with a global `Alert.objects.order_by(
"-received_at")` query scoped only by source, so concurrent same-source pushes can route on each
other's alerts. Make `ProcessingResult` carry the alerts the call actually touched and select
from those.

**Files:**
- Modify: `apps/alerts/services.py:91-101` (`ProcessingResult`), `:255-273` (`_create_alert`),
  `:329` (`_update_alert`), and the `_severity_rank` method (~`:418`)
- Modify: `apps/alerts/check_integration.py:436-459` (`run_checks_and_alert` aggregation)
- Modify: `apps/orchestration/executors.py:87-99` (`IngestExecutor` subject selection)
- Modify: `apps/orchestration/dtos.py` (`IngestResult`)
- Test: `apps/orchestration/_tests/test_executors.py`, `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing tests**

In `apps/alerts/_tests/test_services.py`:

```python
def test_processing_result_collects_the_alerts_it_touched(self):
    from apps.alerts.services import AlertOrchestrator

    payload = {"alerts": [
        {"labels": {"alertname": "cpu", "instance": "a"}, "status": "firing"},
        {"labels": {"alertname": "disk", "instance": "a"}, "status": "firing"},
    ]}
    result = AlertOrchestrator().process_webhook(payload, driver="alertmanager")
    self.assertEqual({a.name for a in result.alerts}, {"cpu", "disk"})
```

In `apps/orchestration/_tests/test_executors.py`:

```python
def test_ingest_subject_is_the_most_severe_alert_from_this_push(self):
    # A pre-existing newer alert from another push must not be chosen.
    ...  # create an unrelated critical Alert with a later received_at
    result = IngestExecutor().execute(ctx)
    self.assertEqual(result.alert_id, <id of this push's critical alert>)

def test_ingest_subject_ties_break_by_name(self):
    # two warnings in one push -> alphabetically first name wins
    ...
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/alerts/_tests/test_services.py -k processing_result_collects -v
```

Expected: FAIL — `ProcessingResult` has no attribute `alerts`.

**Step 3: Implement**

`apps/alerts/services.py` — promote the severity rank to a module-level function (the method
currently duplicates the table) and have the method delegate:

```python
_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


def severity_rank(severity: str) -> int:
    """Numeric rank for severity comparison; unknown severities rank lowest."""
    return _SEVERITY_RANK.get(severity, 0)
```

Change `AlertOrchestrator._severity_rank` to `return severity_rank(severity)`.

Add the collecting field to `ProcessingResult`:

```python
    alerts: list = field(default_factory=list)
```

Append in both `_create_alert` and `_update_alert`, immediately before each `return alert`:

```python
        result.alerts.append(alert)
```

`apps/alerts/check_integration.py` — inside the `run_checks_and_alert` loop, alongside the other
aggregations:

```python
                result.alerts.extend(processing_result.alerts)
```

and add `alerts: list = field(default_factory=list)` to `CheckAlertResult`.

`apps/orchestration/dtos.py` — add to `IngestResult`:

```python
    alert_id: int | None = None
```

`apps/orchestration/executors.py` — replace the global-query block (lines 87-99) with:

```python
            # Subject = the most severe alert THIS call touched, ties broken by name.
            # Deliberately not a global query: two nodes pushing as source=cluster
            # must never route on each other's alerts.
            from apps.alerts.services import severity_rank

            subject = next(
                iter(
                    sorted(
                        proc_result.alerts,
                        key=lambda a: (-severity_rank(a.severity), a.name),
                    )
                ),
                None,
            )
            if subject is not None:
                result.alert_id = subject.id
                result.incident_id = subject.incident_id
                result.alert_fingerprint = subject.fingerprint
                result.severity = subject.severity
                if subject.incident and subject.incident.title:
                    result.incident_title = subject.incident.title
```

Delete the now-unused `Alert` import if nothing else in the method uses it.

**Step 4: Run tests**

```bash
uv run pytest apps/orchestration/_tests/test_executors.py apps/alerts/_tests/test_services.py -v
uv run pytest
```

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "fix(orchestration): route on an alert from this push, not a global query"
```

---

## Task 3: `stages` replaces the three `run_*` booleans

**Files:**
- Modify: `apps/orchestration/models.py:540-542` (`PipelineDefinition`)
- Create: `apps/orchestration/migrations/0010_pipelinedefinition_stages.py` (schema)
- Create: `apps/orchestration/migrations/0011_backfill_pipeline_stages.py` (data)
- Modify: `apps/orchestration/orchestrator.py:528-536`, `apps/orchestration/admin.py:345-352`,
  `apps/alerts/diagnosis.py:38-56`
- Test: `apps/orchestration/_tests/test_pipeline_definition.py`,
  `apps/orchestration/_tests/test_migrations_backfill.py`

**Step 1: Write the failing tests**

```python
class PipelineStagesValidationTests(TestCase):
    def _p(self, stages):
        return PipelineDefinition(name="p", match=[], stages=stages)

    def test_canonical_subset_is_valid(self):
        self._p(["check", "analyze", "notify"]).full_clean(exclude=["channel"])
        self._p(["notify"]).full_clean(exclude=["channel"])
        self._p([]).full_clean(exclude=["channel"])

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ValidationError):
            self._p(["ingest"]).full_clean(exclude=["channel"])

    def test_out_of_order_rejected(self):
        with self.assertRaises(ValidationError):
            self._p(["notify", "check"]).full_clean(exclude=["channel"])

    def test_duplicates_rejected(self):
        with self.assertRaises(ValidationError):
            self._p(["check", "check"]).full_clean(exclude=["channel"])

    def test_non_list_rejected(self):
        with self.assertRaises(ValidationError):
            self._p("notify").full_clean(exclude=["channel"])
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/orchestration/_tests/test_pipeline_definition.py -k Stages -v
```

Expected: FAIL — unknown field `stages`.

**Step 3: Implement the field and validation**

In `apps/orchestration/models.py`, remove `run_checkers`, `run_intelligence`, `run_notify` and add:

```python
    stages = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Ordered downstream stages, e.g. ["check", "analyze", "notify"]. The entry '
            "stage (ingest for webhook traffic, check for checker-generated runs) is not "
            "listed: it has already run by the time this lane is resolved."
        ),
    )
```

Add to the class:

```python
    #: Downstream stages a lane may select, in execution order. INGEST is absent by
    #: design — routing happens after the entry stage, so no lane can control it.
    ROUTABLE_STAGES = [
        PipelineStage.CHECK.value,
        PipelineStage.ANALYZE.value,
        PipelineStage.NOTIFY.value,
    ]

    def clean(self):
        """Validate ``stages`` as data shape only — no domain knowledge lives here."""
        super().clean()
        stages = self.stages
        if not isinstance(stages, list):
            raise ValidationError({"stages": "stages must be a list."})
        unknown = [s for s in stages if s not in self.ROUTABLE_STAGES]
        if unknown:
            raise ValidationError(
                {"stages": f"Unknown stage(s): {', '.join(str(s) for s in unknown)}."}
            )
        if len(set(stages)) != len(stages):
            raise ValidationError({"stages": "Duplicate stages are not allowed."})
        if [s for s in self.ROUTABLE_STAGES if s in stages] != stages:
            raise ValidationError(
                {"stages": f"stages must follow the order {self.ROUTABLE_STAGES}."}
            )
```

Import `ValidationError` from `django.core.exceptions`.

**Step 4: Generate the schema migration, then hand-write the data migration**

```bash
uv run python manage.py makemigrations orchestration --name pipelinedefinition_stages
```

**Important:** edit the generated migration so it *adds* `stages` but does **not** yet remove the
three booleans — the data migration needs to read them. Move the three `RemoveField` operations
into the end of the data migration below.

Create `apps/orchestration/migrations/0011_backfill_pipeline_stages.py`:

```python
"""Derive ``stages`` from the run_* booleans, then drop them.

Order is load-bearing: the backfill reads the booleans, so the RemoveField
operations must follow it in the same migration.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        stages = []
        if defn.run_checkers:
            stages.append("check")
        if defn.run_intelligence:
            stages.append("analyze")
        if defn.run_notify:
            stages.append("notify")
        defn.stages = stages
        defn.save(update_fields=["stages"])


def backwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        stages = defn.stages or []
        defn.run_checkers = "check" in stages
        defn.run_intelligence = "analyze" in stages
        defn.run_notify = "notify" in stages
        defn.save(update_fields=["run_checkers", "run_intelligence", "run_notify"])


class Migration(migrations.Migration):
    dependencies = [("orchestration", "0010_pipelinedefinition_stages")]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField("pipelinedefinition", "run_checkers"),
        migrations.RemoveField("pipelinedefinition", "run_intelligence"),
        migrations.RemoveField("pipelinedefinition", "run_notify"),
    ]
```

The reverse path needs the boolean columns restored before `backwards` runs; add the matching
`AddField` operations ahead of `RunPython` in the reverse direction by splitting them into a
separate migration if `makemigrations` complains. Verify both directions:

```bash
uv run python manage.py migrate orchestration
uv run python manage.py migrate orchestration 0009
uv run python manage.py migrate orchestration
```

**Step 5: Update the three readers**

`apps/orchestration/orchestrator.py:528-536` — replace the flag block with:

```python
        stages = [PipelineStage(s) for s in matched.stages]
```

(The rest of `_downstream_stages` is rewritten in Task 5; this keeps it compiling and green now.)

`apps/orchestration/admin.py:345-352` — replace the three flag fields with `"stages"` in the
fieldset, and update the help text at `:356` which currently says "Flags select stages".

`apps/alerts/diagnosis.py:38-56` — `_STAGE_META` maps stages to flag attributes. Replace with a
membership test:

```python
# stage -> PipelineRun output-ref attr (None when the stage stores no ref)
_STAGE_META = {
    PipelineStage.INGEST: None,
    PipelineStage.CHECK: "checker_output_ref",
    PipelineStage.ANALYZE: "intelligence_output_ref",
    PipelineStage.NOTIFY: "notify_output_ref",
}


def _is_expected(incident, stage) -> bool:
    """Is this stage expected to run for the incident's routed pipeline?"""
    if stage == PipelineStage.INGEST:
        return True  # entry stage, always expected
    if incident.pipeline_id is None:
        return True  # un-routed: assume the full pipeline
    return stage.value in (incident.pipeline.stages or [])
```

Update every other `_STAGE_META` unpacking site accordingly (it currently unpacks a 2-tuple).

**Step 6: Run tests**

```bash
uv run pytest
```

Fix `apps/alerts/_tests/test_diagnosis.py:34-36`, which constructs a definition with the removed
booleans — replace with `stages=["check", "notify"]`.

**Step 7: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "refactor(orchestration): replace run_* booleans with an ordered stages list"
```

---

## Task 4: `channel` FK replaces the `channels` M2M

Delivery only ever used the alphabetically-first active channel. Make the field match.

**Files:**
- Modify: `apps/orchestration/models.py:543-545`
- Create: `apps/orchestration/migrations/0012_pipelinedefinition_channel.py` (schema + data)
- Modify: `apps/orchestration/executors.py:312`, `apps/orchestration/admin.py:332,376`,
  `apps/checkers/preflight/dashboard.py:99`
- Test: `apps/orchestration/_tests/test_executors.py`, `apps/checkers/_tests/preflight/test_dashboard.py`

**Step 1: Write the failing test**

```python
def test_route_incident_uses_the_single_channel_fk(self):
    channel = NotificationChannel.objects.create(name="ops", is_active=True)
    pipeline = PipelineDefinition.objects.create(name="p", match=[], stages=["notify"],
                                                 channel=channel)
    incident = Incident.objects.create(title="t", severity="critical", pipeline=pipeline)
    ctx = StageContext(..., incident_id=incident.id)
    self.assertEqual(NotifyExecutor()._route_incident(ctx), "ops")

def test_route_incident_returns_none_for_inactive_channel(self):
    ...
```

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/orchestration/_tests/test_executors.py -k channel_fk -v
```

Expected: FAIL — unexpected keyword `channel`.

**Step 3: Implement**

`apps/orchestration/models.py` — replace the M2M with:

```python
    channel = models.ForeignKey(
        "notify.NotificationChannel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pipelines",
        help_text="Channel this lane notifies. One channel: delivery never fanned out.",
    )
```

`apps/orchestration/executors.py:312` — replace with:

```python
        channel = pipeline.channel
        return channel.name if channel and channel.is_active else None
```

Note this preserves today's semantics exactly: an inactive channel yields `None`, and the caller
falls back to payload-driven selection.

**Step 4: Migration**

```bash
uv run python manage.py makemigrations orchestration --name pipelinedefinition_channel
```

Edit it so `AddField(channel)` comes first, then a `RunPython` backfill, then
`RemoveField(channels)`:

```python
def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        # Mirror exactly what delivery selected before this migration.
        chosen = defn.channels.filter(is_active=True).order_by("name").first()
        if chosen is not None:
            defn.channel_id = chosen.id
            defn.save(update_fields=["channel"])


def backwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for defn in PipelineDefinition.objects.all():
        if defn.channel_id:
            defn.channels.set([defn.channel_id])
```

**Step 5: Update remaining readers**

- `apps/orchestration/admin.py:332` — delete `filter_horizontal = ["channels"]`; put `"channel"`
  in the fieldset in its place. Replace the `channel_count` display method at `:376` with the
  channel name.
- `apps/checkers/preflight/dashboard.py:99` — `"channels": defn.channels.count()` becomes
  `"channel": defn.channel.name if defn.channel_id else ""`. Update the template/readers that
  consume that key and the test at `apps/checkers/_tests/preflight/test_dashboard.py:113`.

**Step 6: Run tests and commit**

```bash
uv run pytest
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "refactor(orchestration): single channel FK instead of a fan-out M2M that never fanned out"
```

---

## Task 5: Route on one alert, with `origin` as a fact

**Files:**
- Modify: `apps/orchestration/routing.py:11-25`
- Modify: `apps/orchestration/orchestrator.py:498-536` (`_downstream_stages`) and its call sites
  at `:358` and `:393`
- Test: `apps/orchestration/_tests/test_pipeline_routing.py`,
  `apps/orchestration/_tests/test_orchestrator_routing.py`

**Step 1: Write the failing tests**

```python
class FactsFromAlertTests(TestCase):
    def test_facts_come_from_one_alert_only(self):
        alert = Alert.objects.create(
            name="cpu", fingerprint="fp", source="cluster", severity="critical",
            labels={"instance_id": "node-a", "env": "prod"},
        )
        facts = facts_from_alert(alert, origin="incoming_webhook")
        self.assertEqual(facts["source"], "cluster")
        self.assertEqual(facts["severity"], "critical")
        self.assertEqual(facts["instance"], "node-a")
        self.assertEqual(facts["origin"], "incoming_webhook")
        self.assertEqual(facts["labels"]["env"], "prod")

    def test_instance_falls_through_label_keys(self):
        alert = Alert.objects.create(name="cpu", fingerprint="fp2", source="grafana",
                                     labels={"instance": "10.0.0.7"})
        self.assertEqual(facts_from_alert(alert, "")["instance"], "10.0.0.7")


class OriginMatchingTests(TestCase):
    def test_lane_can_match_on_origin(self):
        p = PipelineDefinition(
            name="p", match=[{"field": "origin", "op": "is", "value": "checker_generated"}]
        )
        self.assertTrue(p.matches({"origin": "checker_generated"}))
        self.assertFalse(p.matches({"origin": "incoming_webhook"}))
```

**Step 2: Run to verify they fail**

```bash
uv run pytest apps/orchestration/_tests/test_pipeline_routing.py -k FactsFromAlert -v
```

Expected: FAIL — no `facts_from_alert`.

**Step 3: Implement**

Replace `facts_from_incident` in `apps/orchestration/routing.py`:

```python
def facts_from_alert(alert, origin: str = "") -> dict:
    """Routing facts for ONE alert.

    Deliberately single-alert: merging an incident's alerts mixed labels from the
    oldest with the source of the newest, so a multi-alert incident routed on a
    mashup of two different alerts (design doc 3.4).
    """
    from apps.alerts.services import instance_key_from_labels

    labels = alert.labels if isinstance(alert.labels, dict) else {}
    return {
        "source": alert.source or "",
        "severity": alert.severity or "",
        "instance": instance_key_from_labels(labels),
        "labels": labels,
        "origin": origin or "",
    }
```

`matches()` needs no change — `_fact` is a plain `facts.get(field)`, so `origin` works as soon as
it is in the dict.

Rewrite `_downstream_stages` in `apps/orchestration/orchestrator.py`:

```python
    def _downstream_stages(
        self, alert_id: int | None, origin: str
    ) -> list[PipelineStage] | None:
        """Stages after the entry stage, from the matched lane.

        Returns ``[]`` when the entry stage produced no alert (nothing to route —
        not an error), and ``None`` when an alert exists but no lane matched, which
        the caller turns into a non-retryable ``no_route`` failure.
        """
        from apps.alerts.models import Alert
        from apps.orchestration.routing import facts_from_alert, resolve_pipeline

        if not alert_id:
            return []

        alert = Alert.objects.filter(id=alert_id).select_related("incident").first()
        if alert is None:
            return []

        matched = resolve_pipeline(facts_from_alert(alert, origin))
        if matched is None:
            return None

        incident = alert.incident
        if incident is not None and incident.pipeline_id != matched.id:
            incident.pipeline = matched
            incident.save(update_fields=["pipeline", "updated_at"])

        return [PipelineStage(s) for s in matched.stages]
```

Update both call sites (`:358` resume path, `:393` fresh path) to pass
`stage_result.alert_id` and `pipeline_run.origin`, and to handle `None`:

```python
                    downstream = self._downstream_stages(alert_id, pipeline_run.origin)
                    if downstream is None:
                        raise StageExecutionError(
                            stage=stage,
                            errors=["no_route: no active pipeline matched this alert"],
                            retryable=False,
                        )
                    active_stages.extend(downstream)
                    final_status = self._final_status(downstream)
```

On the resume path, read `alert_id` from `prev_execution.output_snapshot.get("alert_id")`.

**Step 4: Run tests and commit**

```bash
uv run pytest
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "refactor(orchestration): route on a single alert and match on origin"
```

---

## Task 6: Delete the implicit fallback; seed the lanes

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (`_downstream_stages` — the `default` list is
  already gone after Task 5; confirm nothing reintroduces it)
- Create: `apps/orchestration/migrations/0013_seed_default_lanes.py`
- Test: `apps/orchestration/_tests/test_orchestrator_routing.py`

**Step 1: Write the failing tests**

```python
def test_unmatched_alert_fails_the_run_without_notifying(self):
    PipelineDefinition.objects.all().delete()
    run = orchestrator.start_pipeline(payload, source="grafana")
    result = orchestrator.execute_run(run)
    run.refresh_from_db()
    self.assertEqual(run.status, PipelineStatus.FAILED)
    self.assertIn("no_route", run.last_error_message)
    self.assertFalse(run.last_error_retryable)

def test_seeded_catch_all_preserves_todays_behaviour(self):
    catch_all = PipelineDefinition.objects.get(name="catch-all")
    self.assertEqual(catch_all.match, [])
    self.assertEqual(catch_all.stages, ["check", "analyze", "notify"])

def test_seeded_cluster_lane_omits_check(self):
    lane = PipelineDefinition.objects.get(name="cluster-nodes")
    self.assertEqual(lane.stages, ["analyze", "notify"])
```

**Step 2: Run to verify they fail**

**Step 3: Write the seed migration**

`apps/orchestration/migrations/0013_seed_default_lanes.py`:

```python
"""Seed the two default lanes.

These are ordinary rows, not special cases: the engine knows nothing about
"cluster" or "catch-all". They exist so a fresh install behaves exactly as it did
when the fallback lived in Python, and they are as editable and deletable as any
row an operator adds later.
"""

from django.db import migrations

_LANES = [
    {
        "name": "cluster-nodes",
        "description": "Alerts pushed by a node. CHECK is omitted: the node already ran "
        "its own checkers, so hub-side checks would report the hub's CPU and disk.",
        "match": [{"field": "source", "op": "is", "value": "cluster"}],
        "stages": ["analyze", "notify"],
        "priority": 100,
    },
    {
        "name": "catch-all",
        "description": "Everything else. Replaces the implicit fallback that used to live "
        "in _downstream_stages, as a visible, editable row.",
        "match": [],
        "stages": ["check", "analyze", "notify"],
        "priority": 1000,
    },
]


def forwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    for lane in _LANES:
        PipelineDefinition.objects.get_or_create(name=lane["name"], defaults=lane)


def backwards(apps, schema_editor):
    PipelineDefinition = apps.get_model("orchestration", "PipelineDefinition")
    PipelineDefinition.objects.filter(name__in=[lane["name"] for lane in _LANES]).delete()


class Migration(migrations.Migration):
    dependencies = [("orchestration", "0012_pipelinedefinition_channel")]
    operations = [migrations.RunPython(forwards, backwards)]
```

`get_or_create` keeps the migration safe on installs that already configured a lane by that name.

**Step 4: Run tests and commit**

```bash
uv run pytest
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "feat(orchestration): seed default lanes and fail unmatched traffic instead of defaulting"
```

---

## Task 7: Delete `skip_checkers`

**Files:**
- Modify: `apps/alerts/drivers/base.py:71`, `apps/alerts/drivers/cluster.py:43`,
  `apps/alerts/views.py:81-83`, `apps/orchestration/orchestrator.py:324-325`
- Test: existing driver and webhook tests

**Step 1: Write the failing test**

```python
def test_driver_no_longer_declares_skip_checkers(self):
    from apps.alerts.drivers.cluster import ClusterDriver

    self.assertFalse(hasattr(ClusterDriver, "skip_checkers"))

def test_webhook_wrapper_carries_no_skip_checkers_key(self):
    ...  # POST to the cluster webhook, assert the key is absent from inbound_payload
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

Delete the attribute from `BaseAlertDriver` and its `True` override in `ClusterDriver`; delete the
wrapper assignment in `apps/alerts/views.py`; delete `skip_checkers = payload.get(...)` in the
orchestrator. The cluster lane seeded in Task 6 now carries this behaviour.

**Step 4: Verify no references remain**

```bash
grep -rn "skip_checkers" apps config bin docs --include="*.py" --include="*.sh"
```

Expected: only the design doc and this plan.

**Step 5: Run tests and commit**

```bash
uv run pytest
git add -A
git commit -m "refactor(alerts): delete skip_checkers; the cluster lane carries it as data"
```

---

## Task 8: Route checker-generated runs

`run_pipeline --checks-only` is what `bin/install/cron.sh:74` installs on a five-minute default.
It sets `active_stages = [CHECK]` and terminates at `CHECKED`, so the hub opens incidents about
its own disk and memory and notifies nobody.

**Files:**
- Modify: `apps/orchestration/orchestrator.py:324-334` and the stage loop around `:396-400`
- Modify: `apps/orchestration/dtos.py` (`CheckResult` gains `alert_id`)
- Modify: `apps/orchestration/executors.py` (`CheckExecutor` sets it),
  `apps/alerts/check_integration.py` (`CheckAlertResult.alerts`, done in Task 2)
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing test**

```python
def test_checker_generated_run_routes_and_notifies(self):
    PipelineDefinition.objects.create(
        name="hub-self", match=[{"field": "origin", "op": "is", "value": "checker_generated"}],
        stages=["notify"], priority=10,
    )
    run = orchestrator.start_pipeline(
        {"payload": {}, "checks_only": True}, origin=PipelineOrigin.CHECKER_GENERATED
    )
    result = orchestrator.execute_run(run)
    self.assertIn(PipelineStage.NOTIFY, result.stages_completed)

def test_checker_run_with_no_alerts_ends_at_checked(self):
    # Nothing to route is not an error.
    ...
```

**Step 2: Run to verify it fails**

**Step 3: Implement**

Add `alert_id: int | None = None` to `CheckResult` and set it in `CheckExecutor.execute` using the
same most-severe-then-name selection as Task 2 (extract that selection into a shared helper in
`apps/orchestration/routing.py` rather than duplicating it):

```python
def subject_alert(alerts):
    """Most severe alert in a batch, ties broken by name. None for an empty batch."""
    from apps.alerts.services import severity_rank

    ranked = sorted(alerts, key=lambda a: (-severity_rank(a.severity), a.name))
    return ranked[0] if ranked else None
```

Use it in both `IngestExecutor` and `CheckExecutor`.

In the orchestrator, `checks_only` stops being terminal:

```python
        checks_only = payload.get("checks_only", False)
        if checks_only:
            # CHECK is the entry stage for checker-generated runs; the lane is
            # resolved from the alert it produces, exactly as INGEST's is.
            active_stages = [PipelineStage.CHECK]
            final_status = PipelineStatus.CHECKED  # recomputed once downstream is known
        else:
            active_stages = [PipelineStage.INGEST]
            final_status = PipelineStatus.INGESTED
```

In the stage loop, extend the existing CHECK block so that when CHECK is the *entry* stage (i.e.
`checks_only`), it resolves downstream the same way INGEST does:

```python
                if stage == PipelineStage.CHECK and isinstance(stage_result, CheckResult):
                    pipeline_run.checker_output_ref = stage_result.checker_output_ref or ""
                    pipeline_run.save(update_fields=["checker_output_ref", "updated_at"])

                    if checks_only:
                        downstream = self._downstream_stages(
                            stage_result.alert_id, pipeline_run.origin
                        )
                        if downstream is None:
                            raise StageExecutionError(
                                stage=stage,
                                errors=["no_route: no active pipeline matched this alert"],
                                retryable=False,
                            )
                        active_stages.extend(downstream)
                        final_status = self._final_status(downstream)
```

A lane matching checker-generated traffic that also lists `check` in `stages` would re-enter CHECK;
`_stage_completed` already skips an already-succeeded stage, so this is harmless and visible —
which is the §6 "pure data, no guard" position working as intended. Add a test asserting CHECK
runs exactly once in that case.

**Step 4: Add the hub self-check lane to the seed migration**

Create `apps/orchestration/migrations/0014_seed_hub_self_check_lane.py` adding:

```python
{
    "name": "hub-self-check",
    "description": "The hub's own scheduled checks (bin/install/cron.sh). Without this "
    "lane the hub opens incidents about itself and notifies nobody.",
    "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
    "stages": ["analyze", "notify"],
    "priority": 50,
}
```

**Step 5: Run tests and commit**

```bash
uv run pytest
uv run black . && uv run ruff check . --fix
git add -A
git commit -m "fix(orchestration): route checker-generated runs so the hub monitors itself"
```

---

## Task 9: Documentation

**Files:**
- Modify: `AGENTS.md` (the `PipelineDefinition` description under "Stage configuration")
- Modify: `apps/orchestration/AGENTS.md` — **partially done in Task 3**; that file is canonical
  agent guidance and could not be left teaching `run_notify` while later tasks ran. Verify it is
  current rather than assuming.
- Modify: `docs/Architecture.md:67,151`, `docs/Index.md:767` (field table), `docs/Installation.md:317`,
  `docs/Deployment.md:409-411`, `docs/Setup-Guide.md:551` — all still describe the removed `run_*`
  booleans as the routing mechanism.
- `docs/plans/` is an immutable historical record. Do not touch it.

Add an operator note to `docs/Deployment.md`: migration `0010` is destructive (it drops three
columns after backfilling from them) and `0012` discards surplus `channels` rows. Both want a
SQLite backup taken before `migrate`.

Document: lanes are rows; `stages` excludes the entry stage and why; `origin` is a matchable fact;
unmatched traffic fails with `no_route`; the hub's self-check lane exists and notifies.

State plainly that `reeval_existing` is still a documented exception to "everything passes through
orchestration" — it can still resolve an incident with no run, no trace and no notification.
Leaving that undocumented would make the codebase look more consistent than it is.

```bash
git add -A
git commit -m "docs: describe the row-based routing model"
```

---

## Verification before opening the PR

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run pip-audit --strict --desc
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py check
```

Then a manual end-to-end pass, since the behaviour changes are operator-visible:

```bash
# 1. A webhook push routes through the catch-all lane
uv run python manage.py run_pipeline --sample --source grafana

# 2. The hub's own check now reaches NOTIFY instead of stopping at CHECKED
uv run python manage.py run_pipeline --checks-only

# 3. A quiet diagnostic run still creates nothing
uv run python manage.py run_pipeline --checks-only --no-incidents

# 4. Deleting every lane makes traffic fail loudly rather than notify silently
uv run python manage.py shell -c "
from apps.orchestration.models import PipelineDefinition; PipelineDefinition.objects.all().delete()"
uv run python manage.py run_pipeline --sample   # expect a no_route failure
```

Restore the lanes afterwards by re-running the seed migration:

```bash
uv run python manage.py migrate orchestration 0012 && uv run python manage.py migrate orchestration
```

---

## Risks to watch while executing

1. **Migration ordering.** Tasks 3 and 4 both backfill from a column they then drop. The
   `RunPython` must precede the `RemoveField` in the same migration, and the reverse direction
   needs the column back before the backwards function runs. Test both directions explicitly.
2. **Notification volume changes.** Task 8 adds outbound messages that did not exist before: the
   hub's own scheduled self-checks now notify. This is intended, but it is what an operator will
   notice first after deploying.
3. **`_final_status` with an empty stage list.** A lane with `stages: []` terminates at the entry
   stage's status. Confirm `_final_status([])` returns `INGESTED` for webhook runs and `CHECKED`
   for checker runs rather than raising.
4. **Fan-out is still deferred.** Nothing here fixes the "one push routes one incident" bug
   (design doc 3.10 / §9). Do not let it creep in — it needs the run/stage schema decision and
   retention alongside it.
5. **Re-eval is still a backdoor.** 3.8 is untouched by this plan. A node config change can still
   resolve an incident with no run, no trace and no notification. Do not quietly fix it while
   working on something adjacent; it is deferred on purpose.

---

## Deferred: re-eval through orchestration (3.8)

Postponed out of this plan. The mechanics are small — `apply_node_alert_reeval` opens a
`PipelineRun` with a `trace_id` and lets the matched lane handle the resulting changes — but the
consequence is not: threshold edits would start producing outbound messages where they currently
produce silence. That is a judgement about how operators want to be interrupted, and it deserves
its own decision rather than riding along with a routing refactor.

When it is picked up, the shape is:

- Keep `apply_node_alert_reeval` responsible for the re-score itself; have it open a run for the
  resulting changes rather than mutating and returning silently.
- Guard on `report.changes` so a no-op re-score manufactures no pipeline traffic.
- Preserve the existing `report.resolved_count` guard around `_resolve_incidents_for` — a pure
  severity change must still not auto-resolve a manually reopened incident.
- Use `PipelineOrigin.MANUAL` and pass the affected alert as the routing subject, so the lane is
  resolved by the same rule as every other entry point.

Whether the matched lane includes NOTIFY is the actual decision. A lane with `stages: ["analyze"]`
gives full traceability with no new notifications, which may be the palatable middle.

Files it will touch: `apps/alerts/reeval_existing.py:88-141`, `apps/alerts/admin.py:657`,
`apps/alerts/management/commands/reevaluate_node_alerts.py:48`,
`apps/alerts/_tests/test_reeval_existing.py`.
