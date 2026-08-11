# Base Admin Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the stock Django admin into a real operations surface — monitor the inbox, trace pipelines by node/origin/status, persist and browse preflight, read a merged incident timeline, and see disk trends via inline SVG — with no third-party admin package and no new endpoints.

**Architecture:** Node becomes the uniform "which server" spine (the hub is materialized as a self-node via `settings.INSTANCE_ID`); `PipelineRun` gains a `node` FK and an `origin` enum populated at its single creation chokepoint (`PipelineOrchestrator.start_pipeline`). Everything else is admin-layer work (proxy models, readonly renderers, inline-SVG helper) plus one new node-local model pair for preflight persistence.

**Tech Stack:** Django admin, Django ORM + migrations, pytest + pytest-django, `django-object-actions` (already used), inline SVG (no JS/CDN). `uv run` for all commands.

**Conventions (from AGENTS.md):** absolute imports; 100% branch coverage on changed code; line length 100; Black + Ruff; commit frequently. Run `uv run pytest`, `uv run black .`, `uv run ruff check .` before each commit. Design doc: `docs/plans/2026-08-09-admin-hardening-design.md`.

---

## Phase ordering & dependencies

1. **Phase 1 — Hub self-node** (foundation; Phase 2 depends on it)
2. **Phase 2 — PipelineRun node + origin** (depends on Phase 1)
3. **Phase 3 — Inbox monitor** (depends on Phase 2)
4. **Phase 4 — Preflight persistence** (independent; can run any time)
5. **Phase 5 — Inline SVG sparkline helper** (independent)
6. **Phase 6 — Incident merged timeline** (independent; benefits from Phase 5)
7. **Phase 7 — Navigation / relationship wiring** (depends on 1,2,4,5)

Each task is TDD: write failing test → run it (see it fail) → minimal implementation → run it (see it pass) → commit.

---

## Phase 1 — Hub self-node

### Task 1.1: Add `is_self` field to Node

**Files:**
- Modify: `apps/alerts/models.py` (the `Node` class, ~line 329)
- Test: `apps/alerts/_tests/test_models.py` (create if absent)

**Step 1: Write the failing test**

```python
# apps/alerts/_tests/test_models.py
import pytest
from apps.alerts.models import Node


@pytest.mark.django_db
def test_node_is_self_defaults_false():
    node = Node.objects.create(instance_id="agent-1")
    assert node.is_self is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_models.py::test_node_is_self_defaults_false -v`
Expected: FAIL — `AttributeError`/`FieldError` (no `is_self`).

**Step 3: Add the field**

In `apps/alerts/models.py`, inside `Node`:

```python
    is_self = models.BooleanField(
        default=False,
        help_text="True for the Node representing this hub itself (self-node).",
    )
```

**Step 4: Make the migration and run the test**

Run: `uv run python manage.py makemigrations alerts`
Run: `uv run pytest apps/alerts/_tests/test_models.py::test_node_is_self_defaults_false -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/models.py apps/alerts/migrations/ apps/alerts/_tests/test_models.py
git commit -m "feat(alerts): add is_self flag to Node"
```

### Task 1.2: `Node.ensure_self()` classmethod

**Files:**
- Modify: `apps/alerts/models.py` (`Node`, near `upsert` ~line 361)
- Test: `apps/alerts/_tests/test_models.py`

**Step 1: Write the failing tests**

```python
from django.test import override_settings


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_creates_self_node():
    node = Node.ensure_self()
    assert node is not None
    assert node.instance_id == "hub-xyz"
    assert node.is_self is True


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-xyz")
def test_ensure_self_is_idempotent():
    first = Node.ensure_self()
    second = Node.ensure_self()
    assert first.pk == second.pk
    assert Node.objects.filter(is_self=True).count() == 1


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_ensure_self_noop_when_instance_id_unset():
    assert Node.ensure_self() is None
    assert Node.objects.filter(is_self=True).count() == 0
```

**Step 2: Run to verify they fail**

Run: `uv run pytest apps/alerts/_tests/test_models.py -k ensure_self -v`
Expected: FAIL — `AttributeError: ensure_self`.

**Step 3: Implement**

```python
    @classmethod
    def ensure_self(cls):
        """Upsert the Node representing this hub, keyed by settings.INSTANCE_ID.

        Returns the self-node, or None when INSTANCE_ID is unset (no-op).
        """
        import socket

        from django.conf import settings

        instance_id = getattr(settings, "INSTANCE_ID", "") or ""
        if not instance_id:
            return None
        node, _ = cls.objects.update_or_create(
            instance_id=instance_id,
            defaults={"is_self": True, "hostname": socket.gethostname()},
        )
        return node
```

**Step 4: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/test_models.py -k ensure_self -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/models.py apps/alerts/_tests/test_models.py
git commit -m "feat(alerts): Node.ensure_self() upserts idempotent hub self-node"
```

### Task 1.3: `bootstrap_self_node` management command

**Files:**
- Create: `apps/alerts/management/commands/bootstrap_self_node.py`
- Test: `apps/alerts/_tests/management/test_bootstrap_self_node.py` (add `__init__.py` files as needed)

**Step 1: Write the failing test**

```python
import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.alerts.models import Node


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-1")
def test_bootstrap_creates_self_node(capsys):
    call_command("bootstrap_self_node")
    assert Node.objects.filter(instance_id="hub-1", is_self=True).exists()


@pytest.mark.django_db
@override_settings(INSTANCE_ID="")
def test_bootstrap_warns_when_unset(capsys):
    call_command("bootstrap_self_node")
    assert Node.objects.filter(is_self=True).count() == 0
```

**Step 2: Run to verify fail**

Run: `uv run pytest apps/alerts/_tests/management/test_bootstrap_self_node.py -v`
Expected: FAIL — `CommandError: Unknown command`.

**Step 3: Implement**

```python
# apps/alerts/management/commands/bootstrap_self_node.py
from django.core.management.base import BaseCommand

from apps.alerts.models import Node


class Command(BaseCommand):
    help = "Upsert the Node row representing this hub (self-node) from INSTANCE_ID."

    def handle(self, *args, **options):
        node = Node.ensure_self()
        if node is None:
            self.stdout.write(
                self.style.WARNING("INSTANCE_ID is not set; no self-node created.")
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Self-node ready: {node.instance_id}"))
```

**Step 4: Run to verify pass**

Run: `uv run pytest apps/alerts/_tests/management/test_bootstrap_self_node.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/alerts/management/commands/bootstrap_self_node.py apps/alerts/_tests/management/
git commit -m "feat(alerts): bootstrap_self_node management command"
```

### Task 1.4: Surface `is_self` in NodeAdmin

**Files:**
- Modify: `apps/alerts/admin.py` (`NodeAdmin`)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write the failing test**

```python
from apps.alerts.admin import NodeAdmin


def test_nodeadmin_shows_is_self():
    assert "is_self" in NodeAdmin.list_display
    assert "is_self" in NodeAdmin.list_filter
```

**Step 2: Run to verify fail**

Run: `uv run pytest apps/alerts/_tests/test_admin.py -k is_self -v`
Expected: FAIL

**Step 3: Implement** — add `"is_self"` to `NodeAdmin.list_display` and `list_filter`.

**Step 4: Run to verify pass** — same command → PASS.

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat(alerts): show is_self in NodeAdmin"
```

---

## Phase 2 — PipelineRun node + origin

### Task 2.1: Add `origin` enum + `node` FK to PipelineRun

**Files:**
- Modify: `apps/orchestration/models.py` (`PipelineRun`, and add an `Origin` TextChoices near other choices ~line 11)
- Test: `apps/orchestration/_tests/test_models.py`

**Step 1: Write the failing test**

```python
import pytest
from apps.orchestration.models import PipelineRun, PipelineOrigin


@pytest.mark.django_db
def test_pipelinerun_origin_defaults_incoming():
    run = PipelineRun.objects.create(trace_id="t", run_id="r1")
    assert run.origin == PipelineOrigin.INCOMING_WEBHOOK
    assert run.node is None
```

**Step 2: Run to verify fail**

Run: `uv run pytest apps/orchestration/_tests/test_models.py -k origin -v`
Expected: FAIL — no `PipelineOrigin` / no `origin` field.

**Step 3: Implement**

Add choices class:

```python
class PipelineOrigin(models.TextChoices):
    """How a pipeline run was initiated (orthogonal to which node it concerns)."""

    INCOMING_WEBHOOK = "incoming_webhook", "Incoming webhook"
    CHECKER_GENERATED = "checker_generated", "Checker generated"
    MANUAL = "manual", "Manual / CLI"
```

Add fields to `PipelineRun`:

```python
    node = models.ForeignKey(
        "alerts.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pipeline_runs",
        help_text="Server this run concerns (agent node, or the hub self-node).",
    )
    origin = models.CharField(
        max_length=20,
        choices=PipelineOrigin.choices,
        default=PipelineOrigin.INCOMING_WEBHOOK,
        db_index=True,
        help_text="How this run started (incoming push vs local checker vs manual).",
    )
```

**Step 4: Migrate and run**

Run: `uv run python manage.py makemigrations orchestration`
Run: `uv run pytest apps/orchestration/_tests/test_models.py -k origin -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/orchestration/models.py apps/orchestration/migrations/ apps/orchestration/_tests/test_models.py
git commit -m "feat(orchestration): add node FK and origin to PipelineRun"
```

### Task 2.2: Thread `origin`/`node` through `start_pipeline`

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (`start_pipeline`, ~line 140-172)
- Test: `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing tests**

```python
import pytest
from django.test import override_settings

from apps.orchestration.models import PipelineOrigin
from apps.orchestration.orchestrator import PipelineOrchestrator


@pytest.mark.django_db
@override_settings(INSTANCE_ID="hub-1")
def test_checker_generated_run_gets_self_node():
    run = PipelineOrchestrator().start_pipeline(
        payload={"checks_only": True}, source="cli",
        origin=PipelineOrigin.CHECKER_GENERATED,
    )
    assert run.origin == PipelineOrigin.CHECKER_GENERATED
    assert run.node is not None and run.node.is_self is True


@pytest.mark.django_db
def test_incoming_run_resolves_node_from_instance_id():
    payload = {"payload": {"alerts": [{"labels": {"instance_id": "agent-9"}}]}}
    run = PipelineOrchestrator().start_pipeline(
        payload=payload, source="grafana",
        origin=PipelineOrigin.INCOMING_WEBHOOK,
    )
    assert run.origin == PipelineOrigin.INCOMING_WEBHOOK
    assert run.node is not None and run.node.instance_id == "agent-9"


@pytest.mark.django_db
def test_incoming_run_without_instance_id_has_null_node():
    run = PipelineOrchestrator().start_pipeline(payload={}, source="grafana")
    assert run.node is None
```

> Note: verify the exact instance_id label path against `apps.alerts.services.register_pushing_node` before finalizing the helper (Step 3). Reuse that resolver if one exists rather than re-parsing.

**Step 2: Run to verify fail**

Run: `uv run pytest apps/orchestration/_tests/test_orchestrator.py -k "node or origin" -v`
Expected: FAIL — `start_pipeline` has no `origin` param.

**Step 3: Implement**

Add params + resolution to `start_pipeline`:

```python
    def start_pipeline(
        self,
        payload,
        source="unknown",
        trace_id=None,
        environment="production",
        origin=None,
    ):
        from apps.alerts.models import Node
        from apps.orchestration.models import PipelineOrigin

        if origin is None:
            origin = PipelineOrigin.INCOMING_WEBHOOK
        node = self._resolve_node(payload, origin)
        ...
        pipeline_run = PipelineRun.objects.create(
            ...,
            origin=origin,
            node=node,
        )
```

Add a small resolver (reuse existing instance_id extraction if present):

```python
    @staticmethod
    def _resolve_node(payload, origin):
        from apps.alerts.models import Node
        from apps.orchestration.models import PipelineOrigin

        if origin == PipelineOrigin.CHECKER_GENERATED:
            return Node.ensure_self()
        instance_id = _extract_instance_id(payload)  # reuse services helper
        if not instance_id:
            return None
        return Node.objects.filter(instance_id=instance_id).first()
```

**Step 4: Run to verify pass**

Run: `uv run pytest apps/orchestration/_tests/test_orchestrator.py -k "node or origin" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/_tests/test_orchestrator.py
git commit -m "feat(orchestration): resolve node + origin when starting a pipeline"
```

### Task 2.3: Pass correct origin from each caller

**Files:**
- Modify: `apps/alerts/views.py:84` (webhook → `INCOMING_WEBHOOK`)
- Modify: `apps/orchestration/views.py:77` (→ `INCOMING_WEBHOOK`)
- Modify: `apps/orchestration/management/commands/run_pipeline.py` (~line 138; `--checks-only` → `CHECKER_GENERATED`, else `MANUAL`)
- Test: `apps/alerts/_tests/views/test_webhook.py`, `apps/orchestration/_tests/management/test_run_pipeline.py`

**Step 1: Write failing tests** — assert the created run's `origin` per caller (webhook run → `incoming_webhook`; `run_pipeline --checks-only` → `checker_generated`; `run_pipeline --sample` → `manual`).

**Step 2: Run to verify fail** (origins default-wrong for checks-only/manual).

**Step 3: Implement** — pass `origin=` explicitly at each `start_pipeline` call site.

**Step 4: Run to verify pass.**

**Step 5: Commit**

```bash
git add apps/alerts/views.py apps/orchestration/views.py apps/orchestration/management/commands/run_pipeline.py apps/**/_tests/
git commit -m "feat(orchestration): set pipeline origin at each entry point"
```

### Task 2.4: Backfill migration for existing rows

**Files:**
- Create: `apps/orchestration/migrations/XXXX_backfill_node_origin.py` (data migration)
- Test: `apps/orchestration/_tests/test_migrations_backfill.py`

**Step 1: Write the failing test** — using `django-test-migrations` style or a direct test: create a PipelineRun with `source="cli-test"` and an incident with a node, run the backfill function, assert `origin` inferred (`cli*`→manual, else incoming) and `node` copied from `incident.node`.

**Step 2: Run to verify fail.**

**Step 3: Implement** a reversible data migration:

```python
def forwards(apps, schema_editor):
    PipelineRun = apps.get_model("orchestration", "PipelineRun")
    for run in PipelineRun.objects.all().iterator():
        src = (run.source or "").lower()
        run.origin = "manual" if src.startswith("cli") else "incoming_webhook"
        if run.incident_id and getattr(run.incident, "node_id", None):
            run.node_id = run.incident.node_id
        run.save(update_fields=["origin", "node"])
```

Pair with a no-op `reverse` (fields already nullable/defaulted).

**Step 4: Run to verify pass.**

**Step 5: Commit**

```bash
git add apps/orchestration/migrations/ apps/orchestration/_tests/test_migrations_backfill.py
git commit -m "feat(orchestration): backfill node + origin on existing pipeline runs"
```

### Task 2.5: PipelineRunAdmin filters/columns

**Files:**
- Modify: `apps/orchestration/admin.py` (`PipelineRunAdmin`)
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test** — assert `node`, `origin`, `status` in `list_filter` and `list_display`.

**Step 2–4:** add them; run.

**Step 5: Commit**

```bash
git add apps/orchestration/admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat(orchestration): filter/group PipelineRun by node, origin, status"
```

---

## Phase 3 — Inbox monitor

### Task 3.1: `InboxItem` proxy model + age/stuck helpers

**Files:**
- Modify: `apps/orchestration/models.py` (add proxy + methods)
- Test: `apps/orchestration/_tests/test_models.py`

**Step 1: Write the failing tests**

```python
from datetime import timedelta
from django.utils import timezone
from apps.orchestration.models import InboxItem, PipelineRun, PipelineStatus


@pytest.mark.django_db
def test_inbox_lists_only_pending_and_processing():
    PipelineRun.objects.create(trace_id="t", run_id="a", status=PipelineStatus.PENDING)
    PipelineRun.objects.create(trace_id="t", run_id="b", status=PipelineStatus.NOTIFIED)
    ids = set(InboxItem.objects.values_list("run_id", flat=True))
    assert ids == {"a"}  # default manager filters to inbox states


@pytest.mark.django_db
def test_stuck_true_when_processing_past_cutoff():
    run = PipelineRun.objects.create(trace_id="t", run_id="c",
                                     status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(minutes=30))
    item = InboxItem.objects.get(pk=run.pk)
    assert item.is_stuck(timeout_minutes=15) is True
```

**Step 2: Run to verify fail.**

**Step 3: Implement** a proxy with a filtering manager:

```python
class InboxManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(
            status__in=[PipelineStatus.PENDING, PipelineStatus.PROCESSING]
        )


class InboxItem(PipelineRun):
    objects = InboxManager()

    class Meta:
        proxy = True
        verbose_name = "Inbox item"
        verbose_name_plural = "Inbox"

    def is_stuck(self, timeout_minutes=15):
        if self.status != PipelineStatus.PROCESSING:
            return False
        from django.utils import timezone
        from datetime import timedelta
        return self.updated_at < timezone.now() - timedelta(minutes=timeout_minutes)
```

**Step 4: Run to verify pass.**

**Step 5: Commit**

```bash
git add apps/orchestration/models.py apps/orchestration/_tests/test_models.py
git commit -m "feat(orchestration): InboxItem proxy with age/stuck helpers"
```

### Task 3.2: Extract reusable drain/reclaim helpers

**Files:**
- Modify: `apps/orchestration/management/commands/process_inbox.py` — extract the atomic-claim/drain and reclaim logic into importable functions (e.g. `drain_run(run_id)`, `reclaim_stuck(timeout)`) in a new `apps/orchestration/inbox.py`, and have the command call them.
- Test: `apps/orchestration/_tests/test_inbox.py`

**Step 1–5:** TDD the extracted functions (verify no behavior change vs. existing `process_inbox` tests), then commit. **Do not duplicate claim logic in admin** — admin will call these.

```bash
git commit -m "refactor(orchestration): extract drain/reclaim helpers for reuse"
```

### Task 3.3: Register InboxAdmin with actions

**Files:**
- Modify: `apps/orchestration/admin.py`
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write failing tests** — `InboxItem` is registered; admin has actions `drain_selected` and `reclaim_stuck`; a `stuck` display + `age` column exist; changelist is readonly (no add).

**Step 3: Implement** `@admin.register(InboxItem)` with `list_display` (run_id, source, node, origin, status, age, stuck), oldest-first ordering, and two actions delegating to `apps.orchestration.inbox` helpers. `has_add_permission → False`.

**Step 5: Commit**

```bash
git add apps/orchestration/admin.py apps/orchestration/_tests/test_admin.py
git commit -m "feat(orchestration): admin Inbox monitor with drain/reclaim actions"
```

---

## Phase 4 — Preflight persistence

### Task 4.1: `PreflightRun` + `PreflightCheck` models

**Files:**
- Modify: `apps/checkers/models.py`
- Test: `apps/checkers/_tests/test_preflight_models.py`

**Step 1: Write failing tests** — create a `PreflightRun` with counts + `overall_status`; add `PreflightCheck` children (name, level, message, hint); assert relationship + ordering (newest first).

**Step 3: Implement**

```python
class PreflightRun(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    instance_id = models.CharField(max_length=255, blank=True, default="")
    passed = models.PositiveIntegerField(default=0)
    warnings = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    overall_status = models.CharField(max_length=10, default="ok")  # ok|warn|error
    triggered_by = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]


class PreflightCheck(models.Model):
    run = models.ForeignKey(PreflightRun, on_delete=models.CASCADE,
                            related_name="checks")
    name = models.CharField(max_length=100)
    level = models.CharField(max_length=10)  # ok|info|warn|error
    message = models.TextField(blank=True, default="")
    hint = models.TextField(blank=True, default="")
```

**Step 4: Migrate + run.** **Step 5: Commit**

```bash
git add apps/checkers/models.py apps/checkers/migrations/ apps/checkers/_tests/test_preflight_models.py
git commit -m "feat(checkers): PreflightRun + PreflightCheck models"
```

### Task 4.2: Persist from the preflight command (default on, `--no-save`)

**Files:**
- Modify: `apps/checkers/management/commands/preflight.py` (`add_arguments`, `handle`)
- Test: `apps/checkers/_tests/test_preflight_command.py`

**Step 1: Write failing tests**

```python
@pytest.mark.django_db
def test_preflight_persists_by_default():
    call_command("preflight")
    assert PreflightRun.objects.count() == 1
    assert PreflightRun.objects.first().checks.exists()


@pytest.mark.django_db
def test_preflight_no_save_skips_persistence():
    call_command("preflight", "--no-save")
    assert PreflightRun.objects.count() == 0


@pytest.mark.django_db
def test_preflight_counts_match_results():
    call_command("preflight")
    run = PreflightRun.objects.first()
    assert run.passed + run.warnings + run.errors == run.checks.count()
```

**Step 2: Run to verify fail.**

**Step 3: Implement** — add `--no-save` arg; after computing `all_checks`/`passed`/`warnings`/`errors`, unless `--no-save`, create a `PreflightRun` + bulk-create `PreflightCheck`s (`overall_status` = error>warn>ok). Keep `--json` output path intact. Note: `passed` counts `ok`+`info` levels (match existing command logic).

**Step 4: Run to verify pass.** **Step 5: Commit**

```bash
git add apps/checkers/management/commands/preflight.py apps/checkers/_tests/test_preflight_command.py
git commit -m "feat(checkers): persist preflight runs by default (--no-save opt-out)"
```

### Task 4.3: PreflightRun admin + retention note

**Files:**
- Modify: `apps/checkers/admin.py`
- Test: `apps/checkers/_tests/test_admin.py`

**Step 1: Write failing test** — `PreflightRun` registered; `list_display` has counts + `overall_status`; `date_hierarchy="created_at"`; readonly inline of `PreflightCheck`; readonly (no add/change).

**Step 3: Implement** admin + a `PreflightCheckInline` (TabularInline, readonly). Add a `# Retention:` comment documenting a prune approach (e.g. keep last N / older-than pruning via a future cron) — no pruning command built now (YAGNI).

**Step 5: Commit**

```bash
git add apps/checkers/admin.py apps/checkers/_tests/test_admin.py
git commit -m "feat(checkers): admin for persisted preflight runs"
```

---

## Phase 5 — Inline SVG sparkline helper

### Task 5.1: `render_sparkline` utility

**Files:**
- Create: `apps/checkers/admin_charts.py` (or `apps/common/svg.py` if a shared util package exists — verify first)
- Test: `apps/checkers/_tests/test_admin_charts.py`

**Step 1: Write failing tests**

```python
from django.utils.safestring import SafeString
from apps.checkers.admin_charts import render_sparkline


def test_sparkline_returns_inline_svg_safestring():
    out = render_sparkline([(1, 10.0), (2, 20.0), (3, 15.0)])
    assert isinstance(out, SafeString)
    assert out.startswith("<svg")
    assert "http://" not in out and "https://" not in out  # no external refs


def test_sparkline_empty_series_is_safe():
    out = render_sparkline([])
    assert isinstance(out, SafeString)


def test_sparkline_single_point_does_not_crash():
    out = render_sparkline([(1, 42.0)])
    assert out.startswith("<svg")


def test_sparkline_renders_markers():
    out = render_sparkline([(1, 10.0), (2, 90.0)], markers=[2])
    assert "circle" in out  # alert marker drawn
```

**Step 2: Run to verify fail.**

**Step 3: Implement** a pure function that maps points to a viewBox polyline (guard against empty/single point / zero range), optionally drawing `<circle>` markers at flagged x-values, returning `mark_safe("<svg ...>...</svg>")`. No `<script>`, no external `href`.

**Step 4: Run to verify pass.** **Step 5: Commit**

```bash
git add apps/checkers/admin_charts.py apps/checkers/_tests/test_admin_charts.py
git commit -m "feat(checkers): inline SVG sparkline renderer (no JS/CDN)"
```

---

## Phase 6 — Incident merged timeline

### Task 6.1: `build_incident_timeline` service

**Files:**
- Create: `apps/alerts/timeline.py`
- Test: `apps/alerts/_tests/test_timeline.py`

**Step 1: Write failing tests** — given an Incident with `AlertHistory` events, related `PipelineRun`/`StageExecution`s, and notification refs, `build_incident_timeline(incident)` returns a single list of entries sorted chronologically, each with `{when, kind, label, detail}`; assert ordering across the three sources and that `trace_id`/`run_id` are present.

**Step 2: Run to verify fail.**

**Step 3: Implement** a pure aggregator that queries the three sources (via existing relations: `incident.history`, `incident.pipeline_runs` → `stage_executions`, notify refs on the run) and merges by timestamp. No side effects.

**Step 4: Run to verify pass.** **Step 5: Commit**

```bash
git add apps/alerts/timeline.py apps/alerts/_tests/test_timeline.py
git commit -m "feat(alerts): merged incident timeline aggregator"
```

### Task 6.2: Render timeline on IncidentAdmin

**Files:**
- Modify: `apps/alerts/admin.py` (`IncidentAdmin` — add a readonly `journey_timeline` display)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write failing test** — `IncidentAdmin` exposes a readonly `journey_timeline` in `readonly_fields`; calling it for an incident returns SafeString HTML containing the expected event labels in order.

**Step 3: Implement** a `@admin.display` method that calls `build_incident_timeline` and renders a compact readonly `<ol>`/table (use `format_html`/`format_html_join`, escape user content). Add to `readonly_fields`/`fieldsets`.

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat(alerts): render merged journey timeline on Incident admin"
```

### Task 6.3: Simplify AlertHistoryAdmin defaults

**Files:**
- Modify: `apps/alerts/admin.py` (`AlertHistoryAdmin`)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write failing test** — a human `event_label` display exists; `details` JSON is rendered collapsed/pretty via a display method rather than raw; `list_filter` includes `event` and a date filter.

**Step 3: Implement** the display helpers; keep readonly/audit semantics.

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat(alerts): simplify AlertHistory admin readability"
```

---

## Phase 7 — Navigation / relationship wiring

### Task 7.1: Node page inlines + disk sparkline

**Files:**
- Modify: `apps/alerts/admin.py` (`NodeAdmin`)
- Test: `apps/alerts/_tests/test_admin.py`

**Step 1: Write failing tests** — `NodeAdmin` has a readonly `disk_sparkline` display that pulls this node's disk `CheckRun` metric history and returns SafeString SVG; a readonly inline/list of recent pipeline runs for the node exists.

**Step 3: Implement** — `disk_sparkline` display calling `render_sparkline` over `CheckRun` disk metrics for the node (with alert-firing markers); a `PipelineRunInline` (readonly, recent N) or a `@admin.display` list of recent runs. Reuse existing metric extraction from `CheckRunAdmin` if present (DRY).

**Step 5: Commit**

```bash
git add apps/alerts/admin.py apps/alerts/_tests/test_admin.py
git commit -m "feat(alerts): Node page shows disk sparkline + recent pipelines"
```

### Task 7.2: Cross-links PipelineRun ↔ Node ↔ Incident

**Files:**
- Modify: `apps/orchestration/admin.py`, `apps/alerts/admin.py`
- Test: respective `_tests/test_admin.py`

**Step 1: Write failing tests** — `PipelineRunAdmin` renders admin-URL links to its `node` and `incident`; `IncidentAdmin` links to its node.

**Step 3: Implement** `@admin.display` methods returning `format_html('<a href="{}">{}</a>', reverse(...), label)`.

**Step 5: Commit**

```bash
git add apps/orchestration/admin.py apps/alerts/admin.py apps/**/_tests/test_admin.py
git commit -m "feat(admin): cross-link PipelineRun, Node, and Incident"
```

---

## Final verification

**Step 1: Full suite + coverage**

Run:
```bash
uv run coverage run -m pytest && uv run coverage report
uv run black . --check
uv run ruff check .
uv run python manage.py makemigrations --check --dry-run
uv run bandit -r apps/ config/ -c pyproject.toml
```
Expected: all green; 100% branch coverage on changed lines; no missing migrations.

**Step 2: Manual smoke (optional)**
```bash
uv run python manage.py bootstrap_self_node
uv run python manage.py preflight        # then check admin: Preflight runs
uv run python manage.py runserver        # inspect Inbox, Pipeline filters, Incident timeline, Node sparkline
```

**Step 3: Update docs** — note new command (`bootstrap_self_node`), `preflight --no-save`, and new admin surfaces in the relevant `AGENTS.md` files (`apps/alerts`, `apps/checkers`, `apps/orchestration`) and any `docs/` admin section. Commit.

```bash
git commit -m "docs(admin): document self-node, preflight persistence, admin surfaces"
```

## Acceptance criteria (from design)

- Inbox monitorable (pending/processing/stuck) with drain/reclaim from admin.
- Pipelines filterable by node (hub self-node + agents), origin, status.
- Preflight persisted by default and browsable with history.
- Incident page shows a single readable merged timeline.
- Node/Incident pages show disk sparklines with alert markers (inline SVG only).
- No Unfold, no new endpoints; CI green; 100% branch coverage on changed lines.
