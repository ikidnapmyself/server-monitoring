---
title: "Sysadmin Console PR 7: Runs and Inbox"
parent: Plans
---

{% raw %}

# Sysadmin console PR 7: runs and inbox

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Pipeline runs and the inbox get console pages with Retry, Mark failed, Drain and
Reclaim. `PipelineRunAdmin`, `StageExecutionAdmin`, `InboxAdmin` and `IncidentAdmin` drop to
stock and read-only, and the dead Redis health check goes.

**Architecture:** `apps/orchestration/inbox.py` owns every write: `requeue`, `abandon`,
`drain_each` and `pending_pks` join `claim`, `drain_run` and `reclaim_stuck`. `apps/console`
owns the reads (`filters.py`, `queries/runs.py`, `queries/run.py`, `queries/inbox.py`,
`queries/stages.py`) and the pages (`views/runs.py`, `views/run.py`, `views/inbox.py`).
The runs list and the inbox follow the alert list's chips and outerHTML polling. The run page
follows the incident page's wrapper polling, `Notice` and 303 flow. Design:
`docs/plans/2026-10-17-sysadmin-console-pr7-runs-design.md`.

**Tech stack:** Django 5 views and templates, htmx, pytest-django, bats.

**Rules for every task:** absolute imports. No comments that restate code. A line length of 100.
Every wording a describer or a notice writes has no UUID, no `snake_case` token and no `{`.
`Alert.started_at` is NOT NULL, so every fixture alert sets it. No test drains a run that
could reach a real channel: patch `apps.orchestration.inbox.PipelineOrchestrator.execute_run`,
as `apps/orchestration/_tests/test_inbox.py` does, or patch the executor classes in
`apps.orchestration.executors` when the test is about the stage loop itself. Run
`uv run black . && uv run ruff check . --fix` before each commit. End each commit message with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Where this plan corrects the design

1. **Retry would crash on the stage it re-runs.** `StageExecution` has
   `UniqueConstraint(fields=["pipeline_run", "stage", "attempt"], name="unique_stage_attempt")`,
   and `_execute_stage_with_retry` always numbers attempts from 1. A requeued run that failed at
   notify already holds notify attempts 1 to 3, so the re-run's first `create` raises
   `IntegrityError` and the run fails again. A probe against today's code confirmed it.
   `resume_pipeline` has the same bug. Its tests patch `_execute_stage_with_retry` whole, which
   hides it. Task 1 numbers new attempts after the stage's highest existing one.
2. **`inbox.drain` doesn't isolate runs.** One run that raises aborts the loop, so "caught per
   run and reported as a count" can't come from `inbox.drain(limit=25)`. The per-row loop lives
   in `InboxAdmin.drain_selected`, which this PR deletes. Task 1 lifts it into
   `inbox.drain_each(pks)`, and both drain buttons call that.
3. **`QuerySet.update` skips `auto_now`.** `requeue` and `abandon` set `updated_at` themselves.
   Otherwise a requeued run would look stuck the moment a drain claimed it, because
   `reclaim_stuck` and `is_stuck` read `updated_at`.
4. **The node chips break with a stock `IncidentAdmin`.** `render_severity_chips` links
   `?alerts__node__id__exact=…`, a two-relation lookup that `ModelAdmin.lookup_allowed` refuses
   without a matching `list_filter`. Task 9 points the chips at `console:incidents` with
   `node` and `severity`, whose default status `active` is the same open-plus-acknowledged set.
5. **The design names no default status for the runs list.** This plan uses `all`.
6. **Abandon can lose to a live drain.** Mark failed on a `PROCESSING` run that a drain is
   actually executing is overwritten when that drain finishes. The design scopes Mark failed to
   stuck or queued runs, so the plan accepts this and the notice doesn't promise more.

---

### Task 1: Requeue, abandon and an isolated drain

**Files:**
- Modify: `apps/orchestration/orchestrator.py` (`_execute_stage_with_retry`)
- Modify: `apps/orchestration/inbox.py`
- Test: `apps/orchestration/_tests/test_inbox.py`, `apps/orchestration/_tests/test_orchestrator.py`

**Step 1: Write the failing tests**

Append to `apps/orchestration/_tests/test_inbox.py`, using its `_run(run_id, status)` helper:

- `test_requeue_wins_once_and_bumps_attempts`: a failed run whose `updated_at` is two hours
  old. The first `requeue` is `True`, the second `False`. The run is `pending`,
  `total_attempts == 2`, and `updated_at` is fresh.
- `test_requeue_refuses_a_run_that_is_not_failed`, parametrized over every other
  `PipelineStatus` value: `False`, status and attempts unchanged. A missing pk is `False`.
- `test_abandon_fails_an_open_run_once_and_writes_the_error`, parametrized over pending,
  processing and retrying: `True` then `False`. Status `failed`, `last_error_type ==
  "ManualOverride"`, `last_error_message == "Marked failed by an operator"`,
  `last_error_retryable is False`, `completed_at` set.
- `test_abandon_refuses_a_finished_run`, parametrized over the other six statuses.
- `test_drain_each_counts_runs_that_ran_and_runs_that_raised`: runs `ok` and `boom` pending,
  `busy` processing, plus a missing pk. Patch
  `apps.orchestration.inbox.PipelineOrchestrator.execute_run` with a `side_effect` that raises
  for `run.run_id == "boom"`. `drain_each([ok, boom, busy, 999_999])` is `(1, 1)`, and the mock
  saw `["ok", "boom"]` through `call.args[0].run_id`. The patched method is a plain
  `MagicMock`, so `args[0]` is the run, not `self`.
- `test_pending_pks_are_the_oldest_pending_first`, with `created_at` moved back by `.update()`.

The drain test runs the real stage loop. Patching `_execute_stage_with_retry`, as the resume
tests do, would hide the attempt numbering it depends on:

```python
from apps.alerts.models import Alert, Incident
from apps.orchestration.dtos import NotifyResult
from apps.orchestration.executors import AnalyzeExecutor, CheckExecutor, NotifyExecutor
from apps.orchestration.models import PipelineDefinition, StageExecution, StageStatus
from apps.orchestration.testing import clear_lanes


@pytest.mark.django_db
def test_a_requeued_run_that_failed_at_notify_reruns_only_notify():
    clear_lanes()
    PipelineDefinition.objects.create(name="all", stages=["check", "analyze", "notify"])
    incident = Incident.objects.create(severity="critical", subject="disk", instance="web-1")
    Alert.objects.create(
        incident=incident,
        fingerprint="fp",
        name="disk",
        severity="critical",
        status="firing",
        source="cluster",
        started_at=timezone.now(),
    )
    run = PipelineRun.objects.create(
        trace_id="t-requeue",
        run_id="r-requeue",
        status=PipelineStatus.FAILED,
        incident=incident,
        inbound_payload={"downstream_incident_id": incident.pk},
    )
    for stage in ("check", "analyze"):
        StageExecution.objects.create(
            pipeline_run=run, stage=stage, attempt=1, status=StageStatus.SUCCEEDED,
            output_snapshot={"summary": stage},
        )
    for attempt in (1, 2, 3):
        StageExecution.objects.create(
            pipeline_run=run, stage="notify", attempt=attempt, status=StageStatus.FAILED
        )

    assert inbox.requeue(run.pk)
    with (
        patch.object(CheckExecutor, "execute") as check,
        patch.object(AnalyzeExecutor, "execute") as analyze,
        patch.object(
            NotifyExecutor, "execute", return_value=NotifyResult(channels_succeeded=1)
        ) as notify,
    ):
        assert inbox.drain_run("r-requeue") == 1

    run.refresh_from_db()
    assert (run.status, run.run_id, run.trace_id) == (
        PipelineStatus.NOTIFIED, "r-requeue", "t-requeue",
    )
    check.assert_not_called()
    analyze.assert_not_called()
    notify.assert_called_once()
    assert list(
        run.stage_executions.filter(stage="notify")
        .order_by("attempt")
        .values_list("attempt", "status")
    ) == [(1, "failed"), (2, "failed"), (3, "failed"), (4, "succeeded")]
```

Add to `apps/orchestration/_tests/test_orchestrator.py`, next to `SkipCompletedStagesTests`, a
unit test for the numbering on a fresh run: one call of `_execute_stage_with_retry` with
`NotifyExecutor.execute` patched writes attempt 1, and a stage with attempt 2 already stored
writes attempt 3. Set `PipelineOrchestrator(max_retries=1)` so no backoff sleep runs.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/orchestration/_tests/test_inbox.py apps/orchestration/_tests/test_orchestrator.py -q`
Expected: FAIL with `AttributeError: module 'apps.orchestration.inbox' has no attribute
'requeue'`. After the inbox functions exist, the requeue drain test still fails on the unique
constraint until Step 3's orchestrator change lands.

**Step 3: Implement**

In `_execute_stage_with_retry`, add `from django.db.models import Max` to the module imports and
number attempts after what the stage already stored:

```python
        last_error: Exception | None = None
        last_result = None
        # A requeued run re-runs a stage that already holds failed attempts, and
        # (run, stage, attempt) is unique.
        done = (
            StageExecution.objects.filter(pipeline_run=pipeline_run, stage=stage).aggregate(
                last=Max("attempt")
            )["last"]
            or 0
        )

        for tries in range(1, self.max_retries + 1):
            attempt = done + tries
```

Then replace the four uses of `attempt` that mean "tries in this call" with `tries`: both
`attempt >= self.max_retries` checks and both `self.backoff_factor**attempt`. `attempt` stays in
`StageExecution.objects.create`, the idempotency key, `StageContext` and `SignalTags`.

In `apps/orchestration/inbox.py`, add `from django.db.models import F`, refactor `drain` to read
its pks from `pending_pks`, and add:

```python
ABANDON_STATUSES = (PipelineStatus.PENDING, PipelineStatus.PROCESSING, PipelineStatus.RETRYING)
ABANDON_MESSAGE = "Marked failed by an operator"


def requeue(pk: int) -> bool:
    """Put a failed run back in the queue. True iff this call moved it.

    The next drain claims it and ``execute_run`` skips every stage that already succeeded,
    so it keeps its ``run_id`` and ``trace_id`` and re-runs only what failed.
    """
    now = timezone.now()
    return (
        PipelineRun.objects.filter(pk=pk, status=PipelineStatus.FAILED).update(
            status=PipelineStatus.PENDING,
            total_attempts=F("total_attempts") + 1,
            updated_at=now,
        )
        == 1
    )


def abandon(pk: int) -> bool:
    """Fail a queued, claimed or retrying run that should never run. True iff this call did."""
    now = timezone.now()
    return (
        PipelineRun.objects.filter(pk=pk, status__in=ABANDON_STATUSES).update(
            status=PipelineStatus.FAILED,
            last_error_type="ManualOverride",
            last_error_message=ABANDON_MESSAGE,
            last_error_retryable=False,
            completed_at=now,
            updated_at=now,
        )
        == 1
    )


def drain_each(pks: Iterable[int]) -> tuple[int, int]:
    """Claim and execute each run on its own. Return how many ran and how many raised.

    One run that raises is logged and counted, and the rest still run. A run already
    claimed, or gone, is neither.
    """
    ran = failed = 0
    for pk in pks:
        try:
            if claim(pk):
                _execute(PipelineRun(pk=pk))
                ran += 1
        except Exception:  # noqa: BLE001
            logger.exception("Drain failed for run pk=%s", pk)
            failed += 1
    return ran, failed
```

`pending_pks(limit)` is the pk query `drain` runs today, lifted out, and `drain` becomes
`for pk in pending_pks(limit)` with its body unchanged. Update the module
docstring: the console's run and inbox pages are the other caller, not the admin.

**Step 4: Run the orchestration suite**

Run: `uv run pytest apps/orchestration/ -q`
Expected: PASS, including every existing resume and drain test.

**Step 5: Commit**

```bash
git add apps/orchestration/orchestrator.py apps/orchestration/inbox.py apps/orchestration/_tests/
git commit -m "feat(orchestration): requeue and abandon a run, drain runs one by one

A re-run stage numbers its attempts after the ones already stored, so a
retried run no longer collides with unique_stage_attempt."
```

---

### Task 2: Stage output moves to `queries/stages.py`

**Files:**
- Create: `apps/console/queries/stages.py`
- Modify: `apps/console/queries/incident.py`
- Move tests: `apps/console/_tests/queries/test_incident.py` → `apps/console/_tests/queries/test_stages.py`

**Step 1: Move the tests first**

Create `apps/console/_tests/queries/test_stages.py` holding the pure `stage_body` tests from
`test_incident.py`: `test_a_check_snapshot_lists_problems_worst_first_and_counts_the_rest`
through `test_a_malformed_notify_snapshot_does_not_raise`, plus
`test_raw_json_keeps_non_ascii_text_readable`, with the snapshot constants they use.
`test_incident.py` imports back the constants its page-model tests still need. Import
`AnalyzeBody`, `CheckBody`, `NotifyBody`, `RawBody`, `Recommendation` and `stage_body` from
`apps.console.queries.stages`. Delete them from `test_incident.py` and keep there only the
tests that build a page model, which import `CheckBody` from `stages` too.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_stages.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.console.queries.stages'`.

**Step 3: Move the code**

Move from `queries/incident.py` to `queries/stages.py`, unchanged: `CheckBody`,
`Recommendation`, `AnalyzeBody`, `NotifyBody`, `RawBody`, `StageBody`, `TimelineEntry`,
`_text`, `_texts`, `_dicts`, `_check_body`, `_analyze_body`, `_notify_body`, `_BODY_BUILDERS`
and `stage_body`, with the imports they need. `TimelineEntry` moves because the run page's
timeline uses it too. `queries/incident.py` imports `StageBody`, `TimelineEntry` and
`stage_body` from `stages`. Give the module the docstring "How a stage's stored output reads,
for every page that shows one."

**Step 4: Run the console suite**

Run: `uv run pytest apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/queries/ apps/console/_tests/queries/
git commit -m "refactor(console): share stage output between pages"
```

---

### Task 3: `RunFilter` and `InboxFilter`

**Files:**
- Modify: `apps/console/filters.py`
- Modify: `apps/console/queries/incidents.py` (`chip_group` annotation)
- Test: `apps/console/_tests/test_filters.py`

**Step 1: Write the failing tests**

Append to `apps/console/_tests/test_filters.py`:

```python
from apps.console.filters import RUN_STATUS_CHOICES, InboxFilter, RunFilter
from apps.orchestration.models import PipelineStatus


def _run_filter(**params):
    query = QueryDict(mutable=True)
    query.update(params)
    return RunFilter.parse(query)


def test_every_run_status_is_in_exactly_one_bucket():
    buckets = [set(v) for k, v in RUN_STATUS_CHOICES.items() if k != "all"]
    assert set().union(*buckets) == set(PipelineStatus.values)
    assert sum(map(len, buckets)) == len(PipelineStatus.values)


def test_inbox_filter_takes_pending_processing_or_both():
    for raw, status in (("", ""), ("pending", "pending"), ("processing", "processing")):
        assert InboxFilter.parse(QueryDict(f"status={raw}")).status == status
    assert InboxFilter.parse(QueryDict("status=failed")) == InboxFilter(dropped=("status",))
    assert InboxFilter().statuses() == ("pending", "processing")
    assert InboxFilter(status="pending").querystring() == "?status=pending"
    assert InboxFilter().querystring() == ""
```

Add the `RunFilter` cases the same way `AlertFilter`'s are written:
- `RunFilter.parse` of nothing is `RunFilter()`, `statuses()` is `()`, and `active` gives
  `("pending", "processing", "retrying")`.
- `status=failed&origin=bogus&node=3&since=24h&page=0` gives `RunFilter(status="failed",
  node=3, since="24h", dropped=("origin", "page"))`, and `origin=checker_generated` is kept.
- `q` is trimmed, cleaned of NUL and capped at 255.
- `RunFilter(status="failed", page=3).querystring(origin="manual")` is
  `"?status=failed&origin=manual"`, `querystring(page=2)` is `"?status=failed&page=2"`, and
  `kept()` resends status but not `q`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/test_filters.py -q`
Expected: FAIL with `ImportError: cannot import name 'RUN_STATUS_CHOICES'`.

**Step 3: Implement**

In `apps/console/filters.py`, import `PipelineOrigin` and `PipelineStatus` from
`apps.orchestration.models` and add:

```python
_S = PipelineStatus
RUN_STATUS_CHOICES: dict[str, tuple[str, ...]] = {
    "failed": (_S.FAILED.value,),
    "active": (_S.PENDING.value, _S.PROCESSING.value, _S.RETRYING.value),
    "finished": (
        _S.NOTIFIED.value, _S.ANALYZED.value, _S.CHECKED.value, _S.INGESTED.value,
        _S.SKIPPED.value,
    ),
    "all": (),
}
ORIGIN_CHOICES: tuple[str, ...] = ("", *PipelineOrigin.values)
RUN_FIELDS = ("q", "status", "origin", "node", "since", "page")
INBOX_STATUS_CHOICES: dict[str, tuple[str, ...]] = {
    "": (_S.PENDING.value, _S.PROCESSING.value),
    "pending": (_S.PENDING.value,),
    "processing": (_S.PROCESSING.value,),
}
```

`RunFilter` is a frozen dataclass with `q: str = ""`, `status: str = "all"`, `origin: str = ""`,
`node: int | None = None`, `since: str = ""`, `page: int = 1` and `dropped`. Its `parse` calls
`_parse` with `status`, `origin`, `node`, `since` and `page`, then `_text(params, "q",
SEARCH_LIMIT)`. `statuses`, `cutoff`, `querystring`, `without` and `kept` mirror `AlertFilter`,
over `RUN_FIELDS` and `RunFilter()`, and `kept` resends `status`, `origin`, `node` and `since`.

`InboxFilter` has only `status: str = ""` and `dropped`. It has no `page`, so it doesn't go
through `_encode`:

```python
@dataclass(frozen=True)
class InboxFilter:
    status: str = ""
    dropped: tuple[str, ...] = ()

    @classmethod
    def parse(cls, params: QueryDict) -> "InboxFilter":
        values, dropped = _parse(params, (("status", INBOX_STATUS_CHOICES),))
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def statuses(self) -> tuple[str, ...]:
        return INBOX_STATUS_CHOICES[self.status]

    def querystring(self, **changes: str) -> str:
        status = changes.get("status", self.status)
        return f"?{urlencode({'status': status})}" if status else ""
```

Widen the `target` and `default` annotations of `_encode` to `IncidentFilter | AlertFilter |
RunFilter`, and `criteria` in `chip_group` to that union plus `InboxFilter`. `chip_group` only
calls `getattr` and `querystring`, which `InboxFilter` has.

**Step 4: Run them**

Run: `uv run pytest apps/console/_tests/test_filters.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/filters.py apps/console/queries/incidents.py apps/console/_tests/test_filters.py
git commit -m "feat(console): filters for the runs list and the inbox"
```

---

### Task 4: The runs list query

**Files:**
- Create: `apps/console/queries/runs.py`
- Test: `apps/console/_tests/queries/test_runs.py`

**Step 1: Write the failing tests**

Create `apps/console/_tests/queries/test_runs.py` with these helpers:

```python
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.filters import RunFilter
from apps.console.queries import runs as run_queries
from apps.console.queries.runs import build_run_list
from apps.orchestration.models import PipelineRun


def _incident(**fields):
    return Incident.objects.create(severity="critical", status="open", subject="disk", **fields)


def _alert(incident, **fields):
    defaults = {"fingerprint": str(uuid.uuid4()), "name": "disk", "severity": "critical"}
    defaults |= {"status": "firing", "source": "cluster", "started_at": timezone.now()}
    return Alert.objects.create(incident=incident, **(defaults | fields))


def _run(status="notified", incident=None, **fields):
    return PipelineRun.objects.create(
        run_id=str(uuid.uuid4()), trace_id=str(uuid.uuid4()), status=status,
        incident=incident, **fields,
    )
```

Cases, each a `@pytest.mark.django_db` test on `build_run_list(RunFilter(...))`:

- `test_rows_are_newest_first_and_described`: three runs, `created_at` staggered with
  `.update()`. Rows come back newest first. Each row's `what` equals `describe_run(run)`, its
  `url` is `/console/runs/<pk>/`, and its `incident_url` is `/console/incidents/<pk>/` or `""`.
- `test_each_status_chip_selects_its_bucket`, parametrized over `RUN_STATUS_CHOICES`: one run per
  `PipelineStatus` value. The total equals the bucket's size, or every status for `all`. The
  status group's counts match.
- `test_origin_and_since_narrow_the_rows`, freezing `timezone.now` with `monkeypatch` the way
  `test_incidents.py` does.
- `test_q_matches_run_id_trace_id_and_fingerprints`: a run found by a run ID fragment, by a
  trace ID, by its own `alert_fingerprint`, and by the fingerprint of an alert on its incident.
  A run on another incident is not found.
- `test_a_node_matches_by_fk_and_through_the_incidents_alerts`: run A has `node=web`. Run B has
  no node, and its incident's alert is linked to `web`. Run C has no node, and its incident's
  unlinked alert has `labels={"instance_id": "i-web"}`. Run D has `node=db` and an incident
  whose alert names `web`. Filtering on `web` finds A, B and C, not D.
- `test_a_row_names_its_node_or_its_incidents_hosts`: `node` is the node's hostname, else
  `incident_hosts` of its incident, else `""`.
- `test_origin_and_stage_read_as_words`: `origin` is the `PipelineOrigin` label, an unknown
  stored origin reads with spaces for underscores, and `stage` comes from `STAGE_NAMES`.
- `test_an_unknown_node_or_origin_is_dropped_with_a_notice`.
- `test_pages_hold_page_size_rows_and_clamp`, monkeypatching `run_queries.PAGE_SIZE` to 2.
- `test_empty_states`: `"none"` with no runs, `"no_match"` when filters exclude them all.
- `test_the_query_count_does_not_grow_with_the_rows`: `CaptureQueriesContext` over 3 rows and
  over 30 rows gives the same count.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_runs.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_run_list'`.

**Step 3: Implement**

`apps/console/queries/runs.py` follows `queries/alerts.py` line for line where it can. The parts
that differ:

```python
PAGE_SIZE = 50
STATUS_CHIPS: tuple[Choice, ...] = (
    ("failed", "Failed", "failed"),
    ("active", "Queued and in progress", "active"),
    ("finished", "Finished", "finished"),
    ("all", "All", "all"),
)
ORIGIN_CHIPS: tuple[Choice, ...] = (
    ("any", "Any origin", ""),
    *((str(value), str(label), str(value)) for value, label in PipelineOrigin.choices),
)
ORIGIN_LABELS = dict(PipelineOrigin.choices)


def origin_label(origin: str) -> str:
    return str(ORIGIN_LABELS.get(origin, origin.replace("_", " ").capitalize()))


def _matching(criteria: RunFilter, node_keys: tuple[str, ...]):
    runs = PipelineRun.objects.all()
    statuses = criteria.statuses()
    if statuses:
        runs = runs.filter(status__in=statuses)
    if criteria.origin:
        runs = runs.filter(origin=criteria.origin)
    cutoff = criteria.cutoff(timezone.now())
    if cutoff:
        runs = runs.filter(created_at__gte=cutoff)
    if criteria.node is not None:
        # Most producers leave run.node empty, so an unlinked run is about the node its
        # incident's alerts name.
        about = Alert.objects.filter(incident=OuterRef("incident_id")).filter(
            on_node(criteria.node, node_keys)
        )
        runs = runs.filter(Q(node_id=criteria.node) | Q(node__isnull=True) & Exists(about))
    if criteria.q:
        q = criteria.q
        alerts = Alert.objects.filter(incident=OuterRef("incident_id"), fingerprint=q)
        runs = runs.filter(
            Q(run_id__icontains=q)
            | Q(trace_id__icontains=q)
            | Q(alert_fingerprint__icontains=q)
            | Exists(alerts)
        )
    return runs
```

`RunListRow` holds `pk`, `what` (the `describe_run` `Description`), `incident_pk`,
`incident_url`, `node`, `origin`, `stage`, `attempts`, `created_at` and `url`. `_rows` slices
the page from `runs.select_related("node").order_by("-created_at", "-pk")`. It calls
`incident_hosts([...])` once for the page's unlinked runs, so the query count stays flat. `node`
is `alert_host(run.node.hostname, run.node.instance_id, None)` for a linked run, else
`host_summary` of its incident's hosts. `origin` is `origin_label(run.origin)` and `stage` is
`STAGE_NAMES.get(run.current_stage or "", "")`. `_status_counts` aggregates one
`Count("pk", filter=Q(status=s))` per `PipelineStatus` value and sums them per
`RUN_STATUS_CHOICES` key, as `queries/alerts.py` does. `build_run_list(criteria) -> RunList`
mirrors `build_alert_list`: drop an unknown node through `criteria.without("node")`, clamp the
page, and build the groups status (with counts), origin, node (only when nodes exist) and since.
Empty states are `"none"` when no run exists and `"no_match"` otherwise. `RunList` has the
same fields as `AlertList`.

**Step 4: Run them**

Run: `uv run pytest apps/console/_tests/queries/test_runs.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/queries/runs.py apps/console/_tests/queries/test_runs.py
git commit -m "feat(console): the runs list query"
```

---

### Task 5: The run detail query

**Files:**
- Create: `apps/console/queries/run.py`
- Modify: `apps/console/queries/alert.py` (`_incident` becomes public `incident_card`)
- Test: `apps/console/_tests/queries/test_run.py`

**Step 1: Write the failing tests**

Reuse the helpers from `test_runs.py`. Import them from there, the way `test_incident_action.py`
imports `named_incident`. Cases:

- `test_a_missing_run_is_none`.
- `test_the_page_carries_the_runs_description_and_incident_card`: `what.headline` equals
  `describe_run(run).headline`, and `incident` is an `IncidentCard` whose `url` is the console
  incident page.
- `test_every_attempt_is_a_timeline_entry_in_order`: check succeeded, then notify failed on
  attempts 1 and 2, then notify succeeded on attempt 3. Four entries in that order, each `kind ==
  "stage"`, each `id == f"stage-{execution.pk}"`, each `what` built by `describe_stage_outcome`
  plus a `Fact("Attempt", "<n>")`. The check entry's `body` is a `CheckBody`.
- `test_an_execution_that_never_started_sorts_last_and_uses_the_run_time`.
- `test_a_failed_run_offers_retry_only_to_a_user_who_may_act`, parametrized over `can_act`:
  `do == (Action("Retry", "retry_run", {"pk": pk}),)` or `()`.
- `test_an_open_run_offers_mark_failed`, parametrized over pending, processing and retrying.
- `test_a_finished_run_offers_nothing`, parametrized over notified, analyzed, checked, ingested
  and skipped.
- `test_details_hold_the_ids_refs_error_and_payload`: the `what.facts` labels include Run,
  Trace, Status, Fingerprint, the four refs that are set, Error type and Retryable. `raw` is the
  sorted, indented JSON of `inbound_payload`. An empty ref has no fact.
- `test_the_query_count_does_not_grow_with_the_attempts`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_run.py -q`
Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Implement**

Rename `_incident(alert)` in `queries/alert.py` to `incident_card(incident: Incident | None)
-> IncidentCard | None`, taking the incident rather than the alert, and update its one caller.

`apps/console/queries/run.py`:

```python
"""Read model for one pipeline run's page. Returns dataclasses, never querysets."""

RETRY = "Retry"
MARK_FAILED = "Mark failed"
REFS = (
    ("Normalized payload", "normalized_payload_ref"),
    ("Checker output", "checker_output_ref"),
    ("Analysis output", "intelligence_output_ref"),
    ("Notify output", "notify_output_ref"),
)


def _do(run: PipelineRun, can_act: bool) -> tuple[Action, ...]:
    if not can_act:
        return ()
    params = {"pk": run.pk}
    if run.status == PipelineStatus.FAILED:
        return (Action(RETRY, "retry_run", params),)
    if run.status in ABANDON_STATUSES:
        return (Action(MARK_FAILED, "abandon_run", params),)
    return ()


def _facts(run: PipelineRun) -> tuple[Fact, ...]:
    facts = [Fact("Fingerprint", run.alert_fingerprint, copy=True)] if run.alert_fingerprint else []
    facts += [Fact(label, getattr(run, field)) for label, field in REFS if getattr(run, field)]
    if run.last_error_type:
        facts += [
            Fact("Error type", run.last_error_type),
            Fact("Retryable", "yes" if run.last_error_retryable else "no"),
        ]
    return tuple(facts)


def _entry(execution: StageExecution, fallback: datetime) -> TimelineEntry:
    what = describe_stage_outcome(
        execution.stage,
        execution.status,
        execution.error_message,
        retryable=execution.error_retryable,
        duration_ms=execution.duration_ms,
    )
    return TimelineEntry(
        id=f"stage-{execution.pk}",
        when=execution.started_at or fallback,
        kind="stage",
        what=replace(what, facts=(*what.facts, Fact("Attempt", str(execution.attempt)))),
        body=stage_body(execution.stage, execution.output_snapshot),
    )


def build_run_detail(pk: int, *, can_act: bool = False) -> RunDetail | None:
    """One run for its page. ``do`` holds only what the caller may do."""
    run = PipelineRun.objects.select_related("node", "incident").filter(pk=pk).first()
    if run is None:
        return None
    executions = run.stage_executions.order_by(
        F("started_at").asc(nulls_last=True), "attempt", "pk"
    )
    what = describe_run(run)
    hosts = incident_hosts([run.incident_id]) if run.incident_id else {}
    if run.node:
        node = alert_host(run.node.hostname, run.node.instance_id, None)
    else:
        node = ", ".join(hosts.get(run.incident_id, ()))
    return RunDetail(
        pk=run.pk,
        what=replace(what, facts=(*what.facts, *_facts(run))),
        do=_do(run, can_act),
        incident=incident_card(run.incident),
        node=node,
        origin=origin_label(run.origin),
        stage=STAGE_NAMES.get(run.current_stage or "", ""),
        attempts=run.total_attempts,
        created_at=run.created_at,
        timeline=tuple(_entry(e, run.created_at) for e in executions),
        raw=json.dumps(run.inbound_payload, indent=2, sort_keys=True, default=str),
        url=reverse("console:run", args=[run.pk]),
    )
```

`RunDetail` is a frozen dataclass with exactly the fields `build_run_detail` passes.
`run_exists(pk)` is a one-line `.exists()` for the plain POST path in Task 7.
`ABANDON_STATUSES` comes from `apps.orchestration.inbox`, so the button and the write share one
list. `origin_label` comes from `queries/runs.py`.

**Step 4: Run them**

Run: `uv run pytest apps/console/_tests/queries/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console/queries/ apps/console/_tests/queries/test_run.py
git commit -m "feat(console): the run page query"
```

---

### Task 6: Runs list and run page

**Files:**
- Create: `apps/console/views/runs.py`, `apps/console/views/run.py`
- Modify: `apps/console/views/__init__.py`, `apps/console/views/base.py` (gains `Notice`),
  `apps/console/views/incident.py`, `incident_action.py`, `incident_rerun.py` (import `Notice`
  from `base`), `apps/console/urls.py`, `apps/console/intents.py`
- Create: `templates/console/runs.html`, `run.html`, `partials/_runs.html`,
  `partials/_run_row.html`, `partials/_run.html`
- Modify: `templates/console/base.html` (nav), `templates/console/not_found.html`,
  `templates/console/partials/_action.html`
- Test: `apps/console/_tests/views/test_runs.py`, `apps/console/_tests/views/test_run.py`,
  `apps/console/_tests/test_intents.py`

**Step 1: Write the failing tests**

Add to `apps/console/_tests/conftest.py`:

```python
def _perm(user, app_label, codename):
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label=app_label, codename=codename)
    )
    return user


@pytest.fixture
def run_viewer(bare_staff):
    return _perm(bare_staff, "orchestration", "view_pipelinerun")


@pytest.fixture
def run_operator(run_viewer):
    return _perm(run_viewer, "orchestration", "change_pipelinerun")


@pytest.fixture
def inbox_viewer(bare_staff):
    return _perm(bare_staff, "orchestration", "view_inboxitem")


@pytest.fixture
def inbox_operator(inbox_viewer):
    return _perm(inbox_viewer, "orchestration", "change_inboxitem")
```

`bare_staff` is already logged in. Django caches permissions on the user object, so the fixtures
add permissions before any `has_perm` call.

`test_runs.py`, following `test_alerts.py`:
- `test_the_list_route`: `reverse("console:runs") == "/console/runs/"`.
- `test_anonymous_is_sent_to_the_admin_login`.
- `test_staff_without_a_run_permission_is_refused` (`bare_staff`, 403).
- `test_view_or_change_permission_reads_the_list`, parametrized over both codenames.
- `test_the_partial_is_the_polled_root` (HTMX: the body starts with `<div id="runs"`).
- `test_rows_link_to_the_run_and_its_incident`.
- `test_the_nav_marks_runs_current`, with the `_nav` helper from `test_alert.py`.

`test_run.py`, following `test_alert.py` and `test_incident.py`:
- `test_the_page_route`: `reverse("console:run", args=[7]) == "/console/runs/7/"`.
- Anonymous, bare staff 403, and view-or-change reads, as above.
- `test_a_missing_run_is_the_console_404_page`, `@BOTH`, parametrized over `"404"` and a
  26-digit pk. Django 5 answers an out-of-range integer lookup with no rows, which the incident
  page's 404 test already relies on.
- `test_a_viewer_sees_no_do_this_box` and `test_an_operator_sees_retry_on_a_failed_run`.
- `test_the_incident_card_links_the_console_incident`.
- `test_stage_output_renders_through_the_shared_body` (a `CheckBody` problem line appears).
- `test_the_payload_and_ids_render_only_in_details`: the run ID is in the body, and
  `visible(body)` from `test_guard.py` doesn't contain it.
- `test_markup_in_an_error_is_escaped`, with `MARKUP` and `ESCAPED`.
- `test_no_inline_script_or_handler`, scanning the body with `FORBIDDEN` from
  `test_templates.py`.

In `test_intents.py`, move `("view_run", {"pk": 7}, …)` out of
`test_config_intents_point_at_the_admin` into a new parametrized
`test_run_intents_point_at_the_console`. Task 7 adds its other two rows:

```python
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("intent", "url", "method"),
    [("view_run", "/console/runs/7/", "get")],
)
def test_run_intents_point_at_the_console(admin_user, intent, url, method):
    resolved = resolve_action(Action("Go", intent=intent, params={"pk": 7}), admin_user)
    assert resolved == ResolvedAction("Go", url, method)
```

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/views/test_runs.py apps/console/_tests/views/test_run.py apps/console/_tests/test_intents.py -q`
Expected: FAIL with `NoReverseMatch: 'runs' is not a valid view function or pattern name`.

**Step 3: Implement**

Move `Notice` from `views/incident.py` to `views/base.py` unchanged, and import it from there in
the three incident views.

`views/runs.py` holds `RunListView`, a copy of `AlertListView` over `RunFilter`,
`build_run_list`, the `_runs.html` templates, `nav="runs"` and `read_permissions = RUN_READ`.

`views/run.py` holds the shared page view. Task 7 adds the action view to it:

```python
RUN_READ = ("orchestration.view_pipelinerun", "orchestration.change_pipelinerun")
RUN_CHANGE = "orchestration.change_pipelinerun"


class RunPageView(ConsoleView):
    """What the run page and its action view share, so both render one thing."""

    template_name = "console/run.html"
    partial_template_name = "console/partials/_run.html"
    read_permissions = RUN_READ

    @property
    def can_act(self) -> bool:
        return self.request.user.has_perm(RUN_CHANGE)

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "runs"}, status=404)

    def render_run(self, pk: int, notice: Notice | None = None):
        run = build_run_detail(pk, can_act=self.can_act)
        if run is None:
            return self.not_found()
        return self.render_to_response(
            self.get_context_data(run=run, nav="runs", notice=notice)
        )


class RunDetailView(RunPageView):
    def get(self, request, *args, **kwargs):
        return self.render_run(kwargs["pk"])
```

Put `RUN_READ` in `views/run.py` and import it into `views/runs.py`.

URLs, after the alert routes:

```python
    path("runs/", RunListView.as_view(), name="runs"),
    path("runs/<int:pk>/", RunDetailView.as_view(), name="run"),
```

Intents: `view_run` becomes `Intent(_record("console:run"), "get",
"orchestration.view_pipelinerun")`. `retry_run` and `abandon_run` come in Task 7, so until then
`resolve_action` renders the run page's buttons as plain text.

Templates:
- `runs.html` and `partials/_runs.html` copy `alerts.html` and `_alerts.html`. The root id is
  `runs`, the search placeholder is "Run ID, trace ID or fingerprint", and there's no
  `source` group. The status tabs carry the `x-circle`, `sync` and `check-circle` icons for
  failed, active and finished. Blank slates: "No runs yet." ("Runs appear here when an incident
  changes.") and "No runs match these filters." with a Clear filters link.
- `partials/_run_row.html`: icon `_tone_icon.html` with `tone=row.what.tone`, title link
  `id="run-{{ row.pk }}"`, and meta node · origin · stage · attempts · created time · incident
  link `id="run-{{ row.pk }}-incident"`. "Attempt" is pluralized with `pluralize`.
- `run.html` copies `incident.html`: messages outside the wrapper, and `<div id="run" data-poll
  hx-get="{{ run.url }}" hx-trigger="every 30s, console:refresh" hx-swap="innerHTML">`.
- `partials/_run.html` copies `_incident.html`'s shape. It has the notice, a crumb "Back to
  runs", a pagehead with `run.what.headline #pk`, origin, node and created time, and a "What's
  wrong" box rendering `_description.html` with `d=run.what id="run-what" raw=run.raw
  hx_target="#run"`. "Do this" renders each `run.do` through `_action.html` with
  `hx_target="#run"`. Retry is `c-button--primary` and Mark failed is `c-button--danger`. The
  Incident card is copied from `_alert.html`, with `id="run-incident"`. The timeline reuses
  `_timeline_badge.html`, `_entry_lead.html` and `_stage_body.html` exactly as `_incident.html`
  renders a stage entry, with `hx_target="#run"`. The sidebar shows Node, Origin, Stage reached
  and Attempts.
- `base.html`: after Alerts, add Runs (`workflow` icon, `nav == "runs"`). Task 8 adds Inbox.
- `not_found.html`: a `nav == "runs"` branch, "This run doesn't exist. It may have been
  deleted." with a link back to runs.
- `_action.html` gains two optional params for Task 8: `swap`, which defaults to
  `innerHTML` (`hx-swap="{{ swap|default:'innerHTML' }}"`), and `kept`, a list of name and
  value pairs rendered as hidden inputs next to the `all` one.

**Step 4: Run the console suite**

Run: `uv run pytest apps/console/ -q`
Expected: PASS. `test_templates.py` passes, because every icon used exists in the sprite.

**Step 5: Commit**

```bash
git add apps/console templates/console
git commit -m "feat(console): runs list and run page"
```

---

### Task 7: Retry and Mark failed

**Files:**
- Modify: `apps/console/views/run.py`, `apps/console/views/__init__.py`, `apps/console/urls.py`
- Test: `apps/console/_tests/views/test_run_action.py`

**Step 1: Write the failing tests**

Model the file on `test_incident_action.py`, with `_url(run, action)`, `_page(run)`, `_notices`
and `BOTH`. No test here drains, so nothing executes. Cases:

- `test_the_action_lives_under_the_run`: `/console/runs/7/retry/`.
- `test_anonymous_post_is_sent_to_the_admin_login`: `Location` is
  `f"{reverse('admin:login')}?next={_page(run)}"`. The POST can't be the `next`, so
  `landing_path` is the run page.
- `test_anonymous_htmx_post_is_redirected_back_to_the_page_it_came_from`.
- `test_a_viewer_is_refused_and_nothing_changes` (`run_viewer`, `@BOTH`, 403, status unchanged).
- `test_get_is_not_allowed` (405).
- `test_an_unknown_action_is_a_404`, parametrized over `"requeue"`, `"Retry"` and `"drain"`.
- `test_a_missing_run_is_the_console_404_page`, `@BOTH`.
- `test_retry_requeues_a_failed_run`, `@BOTH`: status `pending`, `total_attempts == 2`. Plain
  gets a 303 to the run page, htmx gets 200 with notice `("ok", RETRIED)`.
- `test_retry_on_a_run_that_is_no_longer_failed_warns`, `@BOTH`: a pending run gives notice
  `("warning", "This run is no longer failed.")` and stays unchanged.
- `test_a_double_retry_queues_once`: two htmx POSTs give `ok` then `warning`, and
  `total_attempts == 2`.
- `test_mark_failed_fails_a_queued_run`, parametrized over the three open statuses.
- `test_mark_failed_on_a_finished_run_warns`.
- `test_a_plain_post_shows_its_message_once`: follow the 303. The flash shows on the page, and a
  second GET doesn't show it.
- `test_an_htmx_outcome_never_reaches_the_messages_framework`, copied from the incident test.

Add `("retry_run", "/console/runs/7/retry/", "post")` and
`("abandon_run", "/console/runs/7/abandon/", "post")` to
`test_run_intents_point_at_the_console`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/views/test_run_action.py apps/console/_tests/test_intents.py -q`
Expected: FAIL with `NoReverseMatch: 'run-action' is not a valid view function or pattern
name`.

**Step 3: Implement**

In `views/run.py`, add:

```python
RETRIED = "Run queued again. The next drain runs the stages that didn't finish."
NOT_FAILED = "This run is no longer failed."
ABANDONED = "Run marked failed. No drain will pick it up."
FINISHED = "This run already finished."
ACTIONS: dict[str, tuple[Callable[[int], bool], str, str]] = {
    "retry": (inbox.requeue, RETRIED, NOT_FAILED),
    "abandon": (inbox.abandon, ABANDONED, FINISHED),
}


class RunActionView(RunPageView):
    """Retry or mark failed one run. The rules live in ``apps.orchestration.inbox``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:run", args=[self.kwargs["pk"]])

    def post(self, request, *args, **kwargs):
        if not self.can_act:
            return HttpResponseForbidden("Permission to change runs required.")
        if kwargs["action"] not in ACTIONS:
            raise Http404("Unknown run action.")
        pk = kwargs["pk"]
        write, won, lost = ACTIONS[kwargs["action"]]
        notice = Notice(won, "ok") if write(pk) else Notice(lost, "warning")
        if self.wants_partial:
            # Not the messages framework: the next poll, or an admin page, would eat it.
            return self.render_run(pk, notice=notice)
        if not run_exists(pk):
            return self.not_found()
        messages.add_message(
            request, messages.SUCCESS if notice.tone == "ok" else messages.WARNING, notice.text
        )
        return HttpResponseRedirect(self.landing_path(), status=303)
```

`render_run` returns the 404 page for a run that's gone, so the htmx branch needs no check of
its own. The permission is checked before the action name and the record, as in
`IncidentActionView`. Route and intents:

```python
    path("runs/<int:pk>/<str:action>/", RunActionView.as_view(), name="run-action"),
```

`_run_action(action)` is a copy of `_incident_action` that reverses `console:run-action`.
`retry_run` is `Intent(_run_action("retry"), "post", "orchestration.change_pipelinerun")`, and
`abandon_run` is the same with `"abandon"`.

**Step 4: Run them**

Run: `uv run pytest apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console
git commit -m "feat(console): retry a failed run, mark a stuck one failed"
```

---

### Task 8: The inbox page and its actions

**Files:**
- Create: `apps/console/queries/inbox.py`, `apps/console/views/inbox.py`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`, `apps/console/intents.py`,
  `templates/console/base.html`
- Create: `templates/console/inbox.html`, `templates/console/partials/_inbox.html`
- Test: `apps/console/_tests/queries/test_inbox.py`, `apps/console/_tests/views/test_inbox.py`

**Step 1: Write the failing tests**

`queries/test_inbox.py` on `build_inbox(InboxFilter(...), can_drain=...)`:
- `test_rows_are_pending_and_processing_oldest_first`: finished and failed runs are absent.
- `test_the_status_filter_narrows_to_one_kind`.
- `test_a_processing_run_past_the_stale_window_is_stuck`, using `DEFAULT_STALE_MINUTES` from
  `apps.orchestration.inbox`: one run at `DEFAULT_STALE_MINUTES + 1` minutes old is stuck, one
  at `DEFAULT_STALE_MINUTES - 1` isn't, and a pending run of any age never is.
- `test_counts_cover_the_whole_queue_not_the_shown_rows`: `INBOX_LIMIT` monkeypatched to 2,
  with 3 pending runs. Two rows show, `hidden == 1` and `pending == 3`.
- `test_drain_now_is_offered_on_pending_rows_to_a_drainer_only`, parametrized over `can_drain`.
- `test_page_actions_drain_the_oldest_and_reclaim_only_when_stuck`: with `can_drain`, `do` holds
  `Action("Drain the oldest runs", "drain_inbox")` when anything is pending, plus
  `Action("Return stuck runs to the queue", "reclaim_inbox")` only when a run is stuck.
- `test_an_empty_queue_says_so` (`empty == "drained"`).

`views/test_inbox.py`. Every test that posts a drain uses this autouse fixture:

```python
@pytest.fixture(autouse=True)
def execute_run():
    """A drain in a view test claims for real and executes nothing."""
    with patch("apps.orchestration.inbox.PipelineOrchestrator.execute_run") as mock_exec:
        yield mock_exec
```

Cases:
- Route, anonymous, `bare_staff` 403, view-or-change reads (`inbox_viewer`), and 403 on every
  POST for `inbox_viewer`. The run permissions don't open the inbox, and the inbox permissions
  don't open the runs.
- `test_drain_now_runs_exactly_one_run`, `@BOTH`: two pending runs. POST
  `inbox/<pk>/drain/` for one. `execute_run.call_count == 1`, the posted run is `processing`
  (the mock didn't finish it) and the other is `pending`. The notice is `("ok", "Ran 1 run.")`.
- `test_drain_now_on_a_run_already_claimed_warns`, and the same for a missing pk: `("warning",
  "This run is no longer queued.")`, with no 404, because the inbox page still exists.
- `test_drain_now_that_raises_reports_it`: `execute_run.side_effect = RuntimeError`. Notice
  `("critical", "1 run raised an error. Its page shows where it stopped.")`.
- `test_drain_the_oldest_drains_at_most_the_limit`: monkeypatch `views.inbox.DRAIN_LIMIT` to 2
  with 3 pending runs. Two execute, the newest stays pending, and the notice is `"Ran 2 runs."`.
- `test_drain_the_oldest_on_an_empty_queue_says_nothing_waited` (`info`).
- `test_reclaim_returns_only_stuck_runs`: one stuck, one fresh processing. Notice `"Returned 1
  stuck run to the queue."`, and the fresh one stays processing.
- `test_a_plain_post_redirects_to_the_inbox_with_its_filter`: POST with `status=processing` in
  the body gives a 303 to `/console/inbox/?status=processing`.
- `test_an_htmx_post_keeps_the_filter`: POST with `status=pending` re-renders only pending rows.
- `test_the_partial_is_the_polled_root_and_actions_swap_it_whole`: the body starts with
  `<div id="inbox"`, and every `hx-post` form carries `hx-swap="outerHTML"` and a hidden `status`
  input when a filter is set.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/queries/test_inbox.py apps/console/_tests/views/test_inbox.py -q`
Expected: FAIL with `ModuleNotFoundError`.

**Step 3: Implement**

`apps/console/queries/inbox.py`:

```python
INBOX_LIMIT = 100
STATUS_CHIPS: tuple[Choice, ...] = (
    ("both", "Waiting and in progress", ""),
    ("pending", "Waiting", "pending"),
    ("processing", "In progress", "processing"),
)


def build_inbox(criteria: InboxFilter, *, can_drain: bool = False) -> Inbox:
    base = reverse("console:inbox")
    cutoff = timezone.now() - timedelta(minutes=DEFAULT_STALE_MINUTES)
    is_stuck = Q(status=PipelineStatus.PROCESSING, updated_at__lt=cutoff)
    counts = PipelineRun.objects.aggregate(
        pending=Count("pk", filter=Q(status=PipelineStatus.PENDING)),
        processing=Count("pk", filter=Q(status=PipelineStatus.PROCESSING)),
        stuck=Count("pk", filter=is_stuck),
    )
    runs = PipelineRun.objects.filter(status__in=criteria.statuses())
    total = runs.count()
    found = list(runs.select_related("node").order_by("created_at", "pk")[:INBOX_LIMIT])
    ...
```

`InboxRow` holds `pk`, `what`, `incident_pk`, `incident_url`, `node`, `origin`, `since`,
`stuck`, `drain` (an `Action` or `None`) and `url`. `Inbox` holds `rows`, the `pending`,
`processing` and `stuck` counts, `hidden`, `groups`, `do`, `kept`, `notice`, `empty` and `url`.
Each row's `since` is `updated_at`, the last state change. That's the clock `reclaim_stuck`
reads, and `requeue` refreshes it, so a requeued run doesn't read as days old. `stuck` is
`run.status == PROCESSING and run.updated_at < cutoff`. `drain` is `Action("Drain now",
"drain_run", {"pk": run.pk})` for a pending row when `can_drain`, else `None`. Node and origin
come from the same helpers `queries/runs.py` uses. `do` is described in the tests above.
`kept` is `(("status", criteria.status),)` when a filter is set, for the hidden inputs.
`groups` is one status chip group with `count` from `counts`. The `both` count is
`pending + processing`.

`views/inbox.py`:

```python
INBOX_READ = ("orchestration.view_inboxitem", "orchestration.change_inboxitem")
INBOX_CHANGE = "orchestration.change_inboxitem"
DRAIN_LIMIT = 25


def _runs(count: int) -> str:
    return f"{count} run{'s' if count != 1 else ''}"


def _drained(ran: int, failed: int) -> Notice:
    if failed:
        return Notice(
            f"{_runs(failed)} raised an error. Its page shows where it stopped."
            if failed == 1 and not ran
            else f"Ran {_runs(ran)}. {_runs(failed)} raised an error.",
            "critical",
        )
    return Notice(f"Ran {_runs(ran)}.", "ok")


class InboxPageView(ConsoleView):
    template_name = "console/inbox.html"
    partial_template_name = "console/partials/_inbox.html"
    read_permissions = INBOX_READ

    @property
    def can_drain(self) -> bool:
        return self.request.user.has_perm(INBOX_CHANGE)

    def get(self, request, *args, **kwargs):
        return self.render_inbox(InboxFilter.parse(request.GET))

    def render_inbox(self, criteria: InboxFilter, notice: Notice | None = None):
        inbox_page = build_inbox(criteria, can_drain=self.can_drain)
        return self.render_to_response(
            self.get_context_data(inbox=inbox_page, nav="inbox", notice=notice)
        )


class InboxActionView(InboxPageView):
    """Drain now, drain the oldest, or return stuck runs. The rules live in ``inbox``."""

    http_method_names = ["post"]

    def landing_path(self) -> str:
        return reverse("console:inbox")

    def act(self) -> Notice:
        action = self.kwargs["action"]
        if action == "drain":
            ran, failed = inbox.drain_each([self.kwargs["pk"]])
            if not ran and not failed:
                return Notice("This run is no longer queued.", "warning")
            return _drained(ran, failed)
        if action == "drain-oldest":
            pks = inbox.pending_pks(DRAIN_LIMIT)
            if not pks:
                return Notice("Nothing was waiting.", "info")
            return _drained(*inbox.drain_each(pks))
        count = inbox.reclaim_stuck()
        if not count:
            return Notice("No run was stuck.", "info")
        return Notice(f"Returned {count} stuck run{'s' if count != 1 else ''} to the queue.", "ok")

    def post(self, request, *args, **kwargs):
        if not self.can_drain:
            return HttpResponseForbidden("Permission to drain the inbox required.")
        notice = self.act()
        criteria = InboxFilter.parse(request.POST)
        if self.wants_partial:
            return self.render_inbox(criteria, notice=notice)
        level = {"ok": messages.SUCCESS, "info": messages.INFO, "critical": messages.ERROR}
        messages.add_message(request, level.get(notice.tone, messages.WARNING), notice.text)
        return HttpResponseRedirect(self.landing_path() + criteria.querystring(), status=303)
```

Routes. The action views are three explicit paths, so an unknown action never reaches `act`:

```python
    path("inbox/", InboxPageView.as_view(), name="inbox"),
    path("inbox/drain-oldest/", InboxActionView.as_view(), {"action": "drain-oldest"},
         name="inbox-drain-oldest"),
    path("inbox/reclaim/", InboxActionView.as_view(), {"action": "reclaim"},
         name="inbox-reclaim"),
    path("inbox/<int:pk>/drain/", InboxActionView.as_view(), {"action": "drain"},
         name="inbox-drain"),
```

Intents: `view_inbox` becomes `_page("console:inbox", "status", "status")`, so its `status`
param is the page's filter. `drain_run` is `_record("console:inbox-drain")`, `drain_inbox` is
`_page("console:inbox-drain-oldest")` and `reclaim_inbox` is `_page("console:inbox-reclaim")`,
all `"post"` with `orchestration.change_inboxitem`.

Update `test_query_params_are_encoded` to `/console/inbox/?status=a%26b`, and move `view_inbox`
out of `test_config_intents_point_at_the_admin`.

Templates. `inbox.html` renders messages above the include. `partials/_inbox.html` is the polled
root: `<div id="inbox" data-poll hx-get="{{ inbox.url }}" hx-trigger="every 30s,
console:refresh" hx-swap="outerHTML">`. The notice comes first with `id="inbox-notice"` and
`data-fresh`, as in `_incident.html`. Then the status tabs, then a toolbar holding `inbox.do`,
each rendered with `_action.html` and `hx_target="#inbox" swap="outerHTML" kept=inbox.kept`.
Each row is `id="inbox-run-{{ row.pk }}"` and shows its headline link to the run page, a
`c-label c-tone-critical` "Stuck" label when stuck, node, origin, "since" time and the incident
link. Its `drain` action renders with the same `_action.html` params and
`id="inbox-run-{{ row.pk }}-drain"`. The blank slate for `"drained"` reads "Nothing is
waiting." Show "N more not shown" when `hidden`. Add the Inbox nav item to `base.html` now.

**Step 4: Run the console suite**

Run: `uv run pytest apps/console/ -q`
Expected: PASS. `test_overview.py` still asserts the admin inbox URL until Task 9.

**Step 5: Commit**

```bash
git add apps/console templates/console
git commit -m "feat(console): inbox page with drain and reclaim"
```

---

### Task 9: Point the links at the console

**Files:**
- Modify: `config/dashboard.py`, `apps/alerts/node_overview.py`
- Test: `config/_tests/test_dashboard_readiness.py`, `apps/console/_tests/views/test_overview.py`,
  `apps/console/_tests/views/test_incident.py`, `apps/alerts/_tests/test_node_overview.py`,
  `apps/alerts/_tests/test_node_admin.py`

**Step 1: Update the tests to the new targets**

- `test_dashboard_readiness.py::test_the_inbox_entry_filters_to_the_runs_it_counted`:
  `base = reverse("console:inbox")`, with `?status=pending` and `?status=processing`.
- `test_overview.py:254`: the inbox link is `reverse("console:inbox")`. Line 234's
  `{"status": "processing"}` param now lands as `?status=processing`.
- `test_incident.py:314`: the run entry links `reverse("console:run", args=[run.pk])`.
- `test_node_overview.py`: `build_pipeline_rows` rows link `/console/runs/<pk>/`,
  `build_incident_rows` rows link `/console/incidents/<pk>/`, and
  `test_each_chip_links_to_that_severity_on_the_changelist` becomes
  `test_each_chip_links_to_that_node_and_severity_on_the_console`, asserting
  `/console/incidents/?severity=warning&node=<pk>`.
- `test_node_admin.py:538`: `/console/runs/<pk>/`.

**Step 2: Run them to see them fail**

Run: `uv run pytest config/_tests/test_dashboard_readiness.py apps/alerts/_tests/test_node_overview.py apps/alerts/_tests/test_node_admin.py apps/console/_tests/views/ -q`
Expected: FAIL on the old admin URLs.

**Step 3: Implement**

- `config/dashboard.py`: `inbox_url = reverse("console:inbox")`, with `?status=` and the
  status value. Reword the comment: the inbox page filters on status. Stuck is an age, so
  processing is the nearest filter.
- `apps/alerts/node_overview.py`: `build_incident_rows` uses `reverse("console:incident",
  args=[incident.pk])` and `build_pipeline_rows` uses `reverse("console:run", args=[run.pk])`.
  `render_severity_chips` builds `reverse("console:incidents") + "?" + urlencode({"severity":
  severity, "node": node.pk})`. Rewrite its docstring: the console incident list already
  defaults to unresolved incidents. Drop `UNRESOLVED_INCIDENT_STATUSES` from the chip URL. The
  constant stays for `NodeAdmin`'s counts.

**Step 4: Run them**

Run: `uv run pytest config/ apps/alerts/ apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add config apps/alerts apps/console
git commit -m "feat: runs, inbox and node links open the console"
```

---

### Task 10: The guard test covers runs and the inbox

**Files:**
- Modify: `apps/console/_tests/fixtures.py`, `apps/console/_tests/test_guard.py`

**Step 1: Extend the world**

`build_world` already holds failed runs (every routing gap, the retryable failure) and one stuck
run (`_stalled_incident`). Add the three it lacks:

```python
def _queued_incident() -> Incident:
    incident = _incident("Queued run")
    _alert(incident, "Disk is filling")
    _run(incident, status="pending")
    return incident


def _in_progress_incident() -> Incident:
    incident = _incident("Run in progress")
    _alert(incident, "Load is high")
    run = _run(incident, status="processing")
    _stage(run, "check", output_snapshot=CHECK_SNAPSHOT, duration_ms=41.0)
    _stage(run, "analyze", status="running")
    return incident


def _requeued_incident() -> Incident:
    """A run that failed at notify and was retried: queued again, its old attempts kept."""
    incident = _incident("Requeued run")
    _alert(incident, "Swap is filling up")
    gap = routing_gap("notify", "no_channel", GAP_DETAILS["no_channel"])
    run = _run(incident, status="failed", error=str(gap))
    _stage(run, "check", output_snapshot=CHECK_SNAPSHOT)
    for attempt in (1, 2, 3):
        _stage(
            run, "notify", status="failed", attempt=attempt,
            error_type="StageExecutionError", error_message=gap.errors[0],
            error_retryable=False,
        )
    assert requeue(run.pk)
    return incident
```

The processing run is fresh, so the overview still says "1 run is stuck". Import `requeue` from
`apps.orchestration.inbox`. `World` gains `run_pks: tuple[int, ...]`, read from
`PipelineRun.objects.values_list("pk", flat=True)` at the end of `build_world`.

**Step 2: Extend the guard test**

In `test_no_page_leaks_identifiers`, add these URLs:

```python
        + ["/console/runs/?status=all", "/console/inbox/", "/console/inbox/?status=processing"]
        + [f"/console/runs/{pk}/" for pk in world.run_pks]
```

Add these to the `shown` tuple: "Run queued", "Run in progress", "Retry", "Mark failed",
"Drain now", "Return stuck runs to the queue", "Stuck" and "Diagnose finished".

**Step 3: Run it**

Run: `uv run pytest apps/console/_tests/test_guard.py -q`
Expected: PASS. A failure names the page and the leaked token. Fix the wording at its describer
or template, never in the guard.

**Step 4: Commit**

```bash
git add apps/console/_tests/fixtures.py apps/console/_tests/test_guard.py
git commit -m "test(console): the guard covers runs and the inbox"
```

---

### Task 11: Cut the run, inbox and incident admins to stock

**Files:**
- Modify: `apps/orchestration/admin.py`, `apps/alerts/admin.py`, `config/admin_badges.py`,
  `static/admin/css/ops.css`
- Modify: `apps/orchestration/_tests/test_admin.py`, `apps/alerts/_tests/test_admin.py`,
  `config/_tests/test_admin_badges.py`

**Step 1: Write the failing tests**

Add to `apps/orchestration/_tests/test_admin.py`:

```python
@pytest.mark.django_db
@pytest.mark.parametrize("model", [PipelineRun, StageExecution, InboxItem])
def test_run_admins_are_stock_and_read_only(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert not model_admin.get_actions(request)
    assert not hasattr(model_admin, "change_actions")


@pytest.mark.django_db
def test_a_run_change_page_still_opens(admin_client):
    run = PipelineRun.objects.create(trace_id="t", run_id="r-stock", status="failed")
    url = reverse("admin:orchestration_pipelinerun_change", args=[run.pk])
    assert admin_client.get(url).status_code == 200
```

`get_actions` drops `delete_selected` once delete is refused, so no action is left. Add the
same pair for `Incident` to
`apps/alerts/_tests/test_admin.py` as `TestIncidentAdminIsStock`, next to
`TestAlertAdminIsStock`.

Delete the tests of what goes:
- `apps/orchestration/_tests/test_admin.py`: `test_pipeline_run_detail_shows_flow`,
  `TestPipelineRunObjectActions`, `test_stage_execution_snapshot_pretty`,
  `TestInboxAdmin.test_actions_defined` through `test_reclaim_stuck_scopes_to_selected_pks`
  (`test_age_returns_human_string`, `test_stuck_display_delegates_to_model` and the
  `drain_selected` tests), `TestPipelineRunCrossLinks`, the cross-link half of
  `TestPipelineRunChangePageRendersCrossLinks` (keep
  `test_historical_run_without_an_incident_still_renders`), `TestPipelineFlowLinks` and
  `TestPipelineFlowTones`. Keep `TestPipelineRunAdminFilters` and
  `TestInboxAdminQueryset`, because the stock admins keep those filters and that queryset.
- `apps/alerts/_tests/test_admin.py`: the incident tests in `TestBulkActions` and
  `test_mark_pipelines_for_retry`, `TestPerObjectActions`, `TestIncidentActionMessages`,
  `TestJourneyPanel`, `TestIncidentJourneyTimeline`, `IncidentDiagnosisDisplayTests`,
  `IncidentHeadlineAdminTests` (keep its search test, retargeted at stock `search_fields`),
  `IncidentAdminHostColumnTests`, `NodeLabelTests`, `IncidentPanelLinkTests`,
  `IncidentCountLinkTests`, `IncidentBadgeToneTests`, `IncidentFiringCountToneTests`,
  `IncidentDiagnosisToneTests` and `IncidentJourneyToneTests`. Retarget
  `TestAdminQueryOptimization.test_incident_list_uses_prefetch_related` at
  `list_select_related`. `_manual_runs` goes if nothing else uses it.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/orchestration/_tests/test_admin.py apps/alerts/_tests/test_admin.py -q`
Expected: FAIL on `has_change_permission` and `change_actions`.

**Step 3: Implement**

`apps/orchestration/admin.py`:

```python
class ReadOnlyAdmin(admin.ModelAdmin):
    """Read-only. Operators act on runs and the inbox in the console."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

```

The three admins subclass it with plain options only:
- `PipelineRunAdmin`: `list_display` run ID, status, origin, node, incident, current stage,
  total attempts and created. Today's `list_filter` (`status`, `origin`, `node`, `source`,
  `current_stage`, `environment`) and `search_fields` stay, so `TestPipelineRunAdminFilters`
  holds. `list_select_related = ["node", "incident"]`.
- `StageExecutionAdmin`: today's `list_display`, `list_filter` and `search_fields`, with
  `list_select_related = ["pipeline_run"]`.
- `InboxAdmin`: `ordering = ["created_at"]`, `list_display` run ID, status, origin, node,
  created and updated, `list_filter` status, origin and node, and today's `search_fields`.

`has_add_permission(self, request, obj=None)` covers both the `ModelAdmin` and the inline
signature. Delete `STAGE_GLYPHS`, `StageExecutionInline` and every custom display and action.
`PipelineDefinitionAdmin` stays as it is. Let `uv run ruff check apps/orchestration/admin.py`
list the imports that go unused: `messages`, `PermissionDenied`, `format_html_join`, `timesince`,
`DjangoObjectActions`, `object_action`, `inbox`, `PipelineStatus`, `StageStatus`,
`STAGE_STATUS_TONES`, `tone_for` and `prettify_json`. Keep `JSONEditorWidget`, `db_models`,
`tinted`, `format_html`, `admin_link`, `changelist_link` and `DASH`, which
`PipelineDefinitionAdmin` uses.

`apps/alerts/admin.py`: `IncidentAdmin` becomes a plain `admin.ModelAdmin` shaped like
`AlertAdmin`, with the docstring "Read-only. Operators read and act on incidents in the
console." It has `list_display = ["subject", "instance", "severity", "status", "created_at",
"resolved_at"]`, `list_filter = ["status", "severity"]`, `search_fields = ["subject",
"instance", "summary"]`, `list_select_related = ["node", "pipeline"]`, `date_hierarchy`, and the
three `has_*_permission` methods returning `False`.

Delete `node_label`, `AlertInline` and `PipelineRunInline`. Ruff then lists the imports that
only `IncidentAdmin` used: `describe_incident`, `diagnose_incident`, `IncidentManager`,
`instance_key_from_labels`, `build_incident_timeline`, `InboxItem`, `PipelineRun`,
`DIAGNOSIS_TONES`, `INCIDENT_STATUS_TONES` and `PermissionDenied` if `NodeAdmin` doesn't use
them. `NodeAdmin` keeps `DjangoObjectActions`.

In `config/admin_badges.py`, delete `STAGE_STATUS_TONES` and `DIAGNOSIS_TONES` once
`grep -rn` shows no user, and delete their cases in `config/_tests/test_admin_badges.py`. In
`static/admin/css/ops.css`, delete the `.ops-stage*` and `.ops-diag*` rules.

**Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. `config/_tests/test_dashboard.py:193` still gets a 200 from
`?last_error_type__exact=…&status__exact=failed`, because a local-field lookup needs no
`list_filter`. `config/admin.py`'s `SECTION_MAP` completeness test still passes, because every
model stays registered.

**Step 5: Commit**

```bash
git add -A apps/orchestration apps/alerts config static/admin
git commit -m "feat!: run, inbox and incident admins are stock and read-only

BREAKING CHANGE: Mark for retry, Mark failed, Drain selected, Reclaim stuck
and the incident journey, timeline and Acknowledge/Resolve/Close buttons are
gone from the admin. They live on the console run, inbox and incident pages."
```

---

### Task 12: Drop the Redis health check

**Files:**
- Modify: `bin/lib/health_check.sh`
- Test: `bin/tests/lib/test_health_check.bats`

**Step 1: Write the failing test**

Append to `bin/tests/lib/test_health_check.bats`. Call the function directly, not through
`run`, so the counters stay in this shell. Never assert with `! grep`, because bats ignores its
status.

```bash
# --- run_systemd_checks ---

@test "run_systemd_checks passes a healthy install that runs no Redis" {
    systemctl() { [ "$3" != "redis-server" ] && [ "$3" != "redis" ]; }
    export -f systemctl
    _hc_json_mode=true
    run_systemd_checks
    [ "$_hc_failed" -eq 0 ]
    [[ "${_hc_json_results[*]}" != *redis* ]]
}
```

**Step 2: Run it to see it fail**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_health_check.bats`
Expected: FAIL, `_hc_failed` is 1.

**Step 3: Implement**

Delete the `# Redis` block from `run_systemd_checks` in `bin/lib/health_check.sh`. Run
`grep -rni redis bin/ docs/Installation.md docs/Deployment.md` and drop any line that tells an
operator to install or start Redis for this app. `docs/Security.md`'s Redis mentions are about
the rate-limit cache backend, so they stay.

**Step 4: Run the bats suite**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
Expected: PASS.

**Step 5: Commit**

```bash
git add bin docs
git commit -m "fix(bin): health check no longer fails an install without Redis"
```

---

### Task 13: Docs

**Files:**
- Modify: `apps/orchestration/AGENTS.md`, `apps/console/AGENTS.md`, `AGENTS.md`,
  `docs/Architecture.md`

**Step 1: Edit**

`apps/orchestration/AGENTS.md`:
- Add a short "The queue is the table" section after "Role in the pipeline". It says there's
  no broker. A `PENDING` `PipelineRun` is a queued job, `process_inbox` claims it with
  `inbox.claim`, and `reclaim_stuck` returns a crashed drain's runs. Every operator action is a
  conditional update in `inbox.py`.
- Under "Key modules", list `requeue`, `abandon`, `pending_pks` and `drain_each` in
  `inbox.py`. Say the console's run and inbox pages call them, not the admin. Rewrite the
  `InboxItem` sentence: the proxy stays for its permissions and a read-only admin list.
- Add a "Retry re-queues" paragraph. Retry moves `FAILED → PENDING` and bumps
  `total_attempts`. The next drain skips succeeded stages and numbers re-run attempts after
  the stored ones, because `(run, stage, attempt)` is unique. Nothing executes in the request.
  `max_retries` isn't enforced on an operator retry.
- Under "Django Admin expectations", say the console is the operator surface for runs. The
  run, stage and inbox admins are stock and read-only.

`apps/console/AGENTS.md`, under "Rules":
- The runs list filters on `RunFilter` (`q`, `status` all by default, `origin`, `node`,
  `since`, `page`). `q` matches run ID, trace ID and fingerprint, and an alert fingerprint on
  the run's incident. A node matches the run's FK, or an unlinked run whose incident's alerts
  name it.
- The run page reads with `orchestration.view_pipelinerun` or `change_pipelinerun`. Retry and
  Mark failed need `change_pipelinerun` and go through `RunActionView`. The inbox reads with
  `orchestration.view_inboxitem` or `change_inboxitem`. Drain and reclaim need
  `change_inboxitem`. Neither permission set opens the other page.
- Stage output for every page comes from `queries/stages.py`. Update the "Stage output is
  untrusted in shape" rule, which names `queries/incident.py`.
- An inbox action swaps the polled root whole (`swap="outerHTML"`) and resends the filter as
  `kept` hidden inputs. Drain now executes one run in the request. Never drain in a test
  without patching `PipelineOrchestrator.execute_run`.

`AGENTS.md`: in the console row of the core apps table, or next to rule 3, say the incident
admin is stock and no longer carries the journey. The console incident and run pages do.

`docs/Architecture.md:289`: the Journey panel is now the console incident page's timeline and
the run page.

**Step 2: Verify everything**

```bash
uv run black . --check && uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report --include="apps/console/*,apps/orchestration/inbox.py,apps/orchestration/orchestrator.py,apps/orchestration/admin.py,apps/alerts/admin.py,apps/alerts/node_overview.py,config/dashboard.py"
./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py check
```

Expected: all green, with 100% branch coverage on the changed files. Then open `/console/runs/`,
a failed run, and `/console/inbox/` in a browser against a database with no active channel.
Retry the run, drain it, and watch the notice keep focus through one poll. `console.js` has no
automated test, and the inbox is the first outerHTML root that takes an action.

**Step 3: Commit**

```bash
git add AGENTS.md apps/orchestration/AGENTS.md apps/console/AGENTS.md docs/Architecture.md
git commit -m "docs: console runs and inbox, the queue is the table"
```

{% endraw %}
