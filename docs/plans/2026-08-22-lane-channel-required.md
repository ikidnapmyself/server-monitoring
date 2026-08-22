---
title: "Lane Channel Required — Implementation Plan"
parent: Plans
---

{% raw %}

# A lane delivers to its own channel, or not at all — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan
> task-by-task.

**Goal:** Remove the last implicit fallback in the delivery path. A pipeline lane with no active
channel fails loudly instead of delivering to whatever channel sorts first by name — and a fresh
install is seeded so that failure never fires on a correctly set-up hub.

**Architecture:** Three moves. A migration seeds the default lanes and binds the single active
channel to those that deliver. `NotifySelector.resolve` keeps its default-channel pick but only for
callers that opt in (`allow_default_channel=True`), which the interactive callers do and the
pipeline does not. And `no_channel` is not a second hand-rolled failure beside `no_route` — both
come from one `routing_gap()` factory in a new `apps/orchestration/errors.py`, which also ends the
circular import that kept `StageExecutionError` out of reach of the executors. Readiness and
preflight report lanes that route but cannot deliver.

**Tech stack:** Django 5.2, pytest + pytest-django, `uv`.

Read `docs/plans/2026-08-22-lane-channel-required-design.md` first.

---

## Context the executor needs

### Where things are

- `apps/notify/services.py` — `NotifySelector.resolve` (`:32`); the fallback is the `else` branch
  at `:66-78`.
- `apps/orchestration/executors.py` — `NotifyExecutor.execute`; `_route_incident` (`:329`),
  `_load_incident` (`:315`), the `requested = matched_channel_name or payload.get("notify_driver")`
  line (`:415`), the `NotifySelector.resolve(...)` call (`:433`).
- `apps/orchestration/orchestrator.py` — `StageExecutionError` (`:982`, moved in Task 2);
  `_downstream_or_fail` raises today's `no_route`; `_execute_stage_with_retry` re-raises
  immediately when `retryable=False`, which is what makes both failures terminal.
- `apps/orchestration/models.py` — `PipelineDefinition.routed_channel()` (`:620`) is the ONE rule
  for "does this lane deliver". Never re-derive it.
- `apps/orchestration/migrations/0016_seed_resolved_lane.py` — the seed pattern to copy
  (`get_or_create` on `name`, a `backwards` that deletes only rows still matching the seeded
  shape).
- `config/dashboard.py` — `build_readiness()` (`:31`), entries are
  `{key, label, status, detail, url}`.
- `apps/checkers/preflight/checks.py` — `check_pipeline_state()` (`:451`), returns
  `list[CheckResult]` with `level` in `ok|info|warn|error`.

### Three traps

1. **`StageExecutionError` currently lives in `orchestrator.py`, which imports `executors`** — so
   an executor cannot import it at module level. Task 2 moves it to
   `apps/orchestration/errors.py` rather than working around it with a function-local import.
2. **A lane that lists no `notify` stage needs no channel.** `hub-self-check` has `stages: []`.
   Anything that reports or fails on "no channel" must first ask whether the lane delivers at all —
   `"notify" in lane.routable_stages()`.
3. **`Alert.objects.create(...)` in tests needs `started_at`** (NOT NULL, no default), and a
   `PipelineRun` needs `trace_id`/`run_id`.

### Verify after every task

```bash
uv run pytest apps/orchestration/_tests/ apps/notify/_tests/ apps/checkers/_tests/ -q
uv run black . && uv run ruff check . && uv run python manage.py check
```

Conventions: absolute imports, line length 100, 100% branch coverage on changed code, one commit
per task.

---

## Task 1: `NotifySelector` defaults only when asked

**Files:**
- Modify: `apps/notify/services.py`
- Modify: `apps/notify/views.py` (`:89`), `apps/notify/management/commands/test_notify.py` (`:165`)
- Test: `apps/notify/_tests/test_services.py`

**Step 1: Write the failing tests**

```python
class DefaultChannelIsOptInTests(TestCase):
    """The single-channel default is for callers who ARE the operator.

    Picking "the first active channel ordered by name" is the right answer for
    `test_notify` with no argument, and a silent misroute for a pipeline lane whose
    channel is unset — a critical alert landing in #aaa-general because of its name.
    So the caller opts in rather than the selector assuming.
    """

    def setUp(self):
        from apps.notify.models import NotificationChannel

        NotificationChannel.objects.create(
            name="aaa-first", driver="generic", is_active=True, config={}
        )

    def test_no_provider_and_no_opt_in_selects_no_channel(self):
        from apps.notify.services import NotifySelector

        _, _, _, _, channel_obj, _ = NotifySelector.resolve(None, {})

        self.assertIsNone(channel_obj)

    def test_no_provider_with_opt_in_picks_the_active_channel(self):
        from apps.notify.services import NotifySelector

        _, _, label, _, channel_obj, _ = NotifySelector.resolve(
            None, {}, allow_default_channel=True
        )

        self.assertIsNotNone(channel_obj)
        self.assertEqual(label, "aaa-first")

    def test_a_named_channel_still_wins_without_opt_in(self):
        """Opt-in governs only the no-argument case."""
        from apps.notify.services import NotifySelector

        _, _, label, _, channel_obj, _ = NotifySelector.resolve("aaa-first", {})

        self.assertIsNotNone(channel_obj)
        self.assertEqual(label, "aaa-first")
```

Check the existing test module name in `apps/notify/_tests/` and add to it rather than creating a
duplicate.

**Step 2: Run and watch the first one fail**

```bash
uv run pytest apps/notify/_tests/ -k DefaultChannelIsOptIn -v
```

Expected: `test_no_provider_and_no_opt_in_selects_no_channel` fails (a channel IS selected).

**Step 3: Add the parameter**

Signature gains `allow_default_channel: bool = False`. In the `else` branch, guard the DB lookup:

```python
        else:
            # The single-channel default is opt-in. For an interactive caller
            # ("send a test message") it is the intent; for the pipeline it would be
            # a guess about routing, and picking by name is how a critical alert
            # ends up in whatever channel sorts first. See
            # docs/plans/2026-08-22-lane-channel-required-design.md.
            channel = (
                NotificationChannel.objects.filter(is_active=True).order_by("name").first()
                if allow_default_channel
                else None
            )
```

Update the module docstring's "Selection priority" list to say the second rule is opt-in.

**Step 4: Opt the two interactive callers in**

`apps/notify/views.py:89` and `apps/notify/management/commands/test_notify.py:165` both call
`NotifySelector.resolve(requested, payload_config, ...)`. Add `allow_default_channel=True` to each,
with a short comment: the caller is an operator asking for a send, so "use my channel" is the
intent.

**Step 5: Run**

```bash
uv run pytest apps/notify/_tests/ apps/orchestration/_tests/ -q
```

Expected: PASS. If an orchestration test fails here, it is relying on the pipeline's implicit
default — leave it failing and fix it in Task 2, where that behaviour is replaced deliberately.

**Step 6: Commit**

```bash
git add apps/notify/
git commit -m "refactor(notify): the single-channel default is opt-in"
```

---

## Task 2: One "operator must fix this" failure, raised from two places

`no_route` and `no_channel` are the same kind of failure: the routing table does not say where the
work goes, and no retry can change that. They must not be two hand-rolled `StageExecutionError`
constructions that drift — `_downstream_or_fail`'s docstring already carries the reasoning for
`retryable=False`, and a second copy of that reasoning is how the two stop agreeing.

**Files:**
- Create: `apps/orchestration/errors.py`
- Modify: `apps/orchestration/orchestrator.py` — remove the class body, re-export, use the factory
  in `_downstream_or_fail`
- Modify: `apps/orchestration/executors.py` — `NotifyExecutor.execute`
- Test: `apps/orchestration/_tests/test_executors.py`, `apps/orchestration/_tests/test_errors.py`

**Step 1: Write the failing tests**

For the shared construct (`apps/orchestration/_tests/test_errors.py`):

```python
class RoutingGapTests(SimpleTestCase):
    """One construct for 'the routing table does not say where this goes'.

    no_route (no lane matched) and no_channel (the lane names no active channel)
    differ only in which half is missing. Both are terminal until an operator edits
    a row, so both must be non-retryable — and that must be stated once.
    """

    def test_a_routing_gap_is_never_retryable(self):
        from apps.orchestration.errors import routing_gap

        error = routing_gap("routing", "no_route", "no active pipeline matched this alert")

        self.assertFalse(error.retryable)

    def test_the_code_leads_the_message(self):
        """Operators and log searches key on the code, so it must be the prefix."""
        from apps.orchestration.errors import routing_gap

        error = routing_gap("notify", "no_channel", "the matched lane names no active channel")

        self.assertTrue(error.errors[0].startswith("no_channel: "))

    def test_it_carries_the_stage_it_was_raised_for(self):
        from apps.orchestration.errors import routing_gap

        self.assertEqual(routing_gap("notify", "no_channel", "x").stage, "notify")
```

For the behaviour (`test_executors.py`):

```python
class TestNotifyExecutorNoChannel(TestCase):
    """A lane that routes to NOTIFY but names no active channel fails loudly.

    The alternative is what this replaces: delivering to whatever channel sorts
    first by name, which is silent and wrong rather than loud and fixable.
    """

    def _incident_on_lane(self, channel=None):
        from django.utils import timezone

        from apps.alerts.models import Alert, Incident
        from apps.orchestration.models import PipelineDefinition

        lane = PipelineDefinition.objects.create(
            name="lane", match=[], stages=["notify"], priority=1, channel=channel
        )
        incident = Incident.objects.create(title="Disk", severity="critical", pipeline=lane)
        Alert.objects.create(
            fingerprint="fp-nc",
            source="cluster",
            name="Disk",
            severity="critical",
            started_at=timezone.now(),
            incident=incident,
        )
        return incident

    def test_no_channel_fails_non_retryably(self):
        from apps.orchestration.errors import StageExecutionError

        incident = self._incident_on_lane()

        with self.assertRaises(StageExecutionError) as caught:
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

        self.assertFalse(caught.exception.retryable)
        self.assertIn("no_channel", "; ".join(caught.exception.errors))

    def test_an_inactive_channel_is_no_channel(self):
        """routed_channel() is the one rule for 'active', and notify honours it."""
        from apps.notify.models import NotificationChannel
        from apps.orchestration.errors import StageExecutionError

        channel = NotificationChannel.objects.create(
            name="off", driver="generic", is_active=False, config={}
        )
        incident = self._incident_on_lane(channel=channel)

        with self.assertRaises(StageExecutionError):
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

    def test_a_payload_named_driver_still_sends(self):
        """CLI/manual runs that name their own driver are unaffected."""
        driver_cls, driver_inst = _mock_driver_cls()
        incident = self._incident_on_lane()

        with patch.dict(
            "apps.notify.views.DRIVER_REGISTRY", {"generic": driver_cls}, clear=False
        ):
            result = NotifyExecutor().execute(
                _ctx(payload={"notify_driver": "generic"}, incident_id=incident.id)
            )

        self.assertFalse(result.has_errors)
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/orchestration/_tests/test_errors.py apps/orchestration/_tests/test_executors.py -k "RoutingGap or NoChannel" -v
```

Expected: `ModuleNotFoundError: apps.orchestration.errors`.

**Step 3: Extract the error and add the factory**

Create `apps/orchestration/errors.py` and MOVE `StageExecutionError` there verbatim (it is
currently at `orchestrator.py:982`). Then:

```python
def routing_gap(stage: str, code: str, detail: str) -> StageExecutionError:
    """The routing table does not say where this work goes. Never retryable.

    Two shapes of the same failure: ``no_route`` (no lane matched the alert) and
    ``no_channel`` (the matched lane names no active channel). Neither can be
    fixed by trying again — the alert is stuck until an operator edits a row, and
    a retryable failure would spin forever. One factory so that reasoning is
    stated once and the two cannot drift apart.

    ``code`` leads the message because operators and log searches key on it.
    """
    return StageExecutionError(stage=stage, errors=[f"{code}: {detail}"], retryable=False)
```

In `orchestrator.py`, delete the class body and re-export so existing importers keep working:

```python
from apps.orchestration.errors import StageExecutionError, routing_gap  # noqa: F401
```

Keep a one-line comment saying the re-export is for the many modules and tests that import
`StageExecutionError` from here.

Then rewrite `_downstream_or_fail`'s raise to use the factory:

```python
            raise routing_gap(
                "routing", "no_route", "no active pipeline matched this alert"
            )
```

Its docstring already explains why the failure is attributed to `routing` rather than to the entry
stage, and why it is non-retryable — trim the `retryable=False` half of that reasoning, since the
factory now owns it, and leave the attribution reasoning in place.

**Step 4: Raise it from NotifyExecutor**

In `NotifyExecutor.execute`, right after `requested = matched_channel_name or
payload.get("notify_driver")`:

```python
            if not requested:
                # No lane channel and nothing named by the caller. Same failure as an
                # unroutable alert, in the other half of the routing table: delivering
                # to "the first active channel by name" instead is how a critical
                # alert ends up wherever the alphabet points.
                raise routing_gap(
                    PipelineStage.NOTIFY,
                    "no_channel",
                    "the matched lane names no active notification channel",
                )
```

`from apps.orchestration.errors import routing_gap` goes at module level — the whole point of the
extraction is that this import is no longer circular.

**Step 5: Run the full suite**

```bash
uv run pytest -q
```

Some existing tests will now fail because they relied on the implicit default. For each: if the
test is about delivery, give its lane or payload a channel — that is the setup a real deployment
has. If the test is about something else and merely tripped over notify, the same fix applies. Do
not weaken an assertion, and do not re-add the fallback to make a test pass.

Also check nothing imports `StageExecutionError` in a way the re-export misses:
`grep -rn "StageExecutionError" apps/`.

**Step 6: Commit**

```bash
git add apps/orchestration/
git commit -m "feat(orchestration): a lane with no active channel fails no_channel"
```

---

## Task 3: Seed the lanes and bind the channel

**Files:**
- Create: `apps/orchestration/migrations/0017_bind_lane_channels.py`
- Test: `apps/orchestration/_tests/test_orchestrator_routing.py` (beside `SeededDefaultLanesTests`)

**Step 1: Write the failing tests**

The migration's *effect* is what to test, not the migration mechanics — call the same helper the
migration uses:

```python
class LaneChannelSeedTests(TestCase):
    """A fresh install delivers without hand-configuration — when the answer is unambiguous."""

    def _lane(self, name, stages, channel=None):
        return PipelineDefinition.objects.create(
            name=name, match=[], stages=stages, priority=100, channel=channel
        )

    def _channel(self, name="ops", is_active=True):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=is_active, config={}
        )

    def test_one_active_channel_is_bound_to_delivering_lanes(self):
        from apps.orchestration.seeding import bind_lane_channels

        channel = self._channel()
        lane = self._lane("delivers", ["analyze", "notify"])

        bind_lane_channels(PipelineDefinition, type(channel))

        lane.refresh_from_db()
        self.assertEqual(lane.channel_id, channel.id)

    def test_a_lane_that_never_notifies_is_left_alone(self):
        from apps.orchestration.seeding import bind_lane_channels

        channel = self._channel()
        lane = self._lane("records-only", [])

        bind_lane_channels(PipelineDefinition, type(channel))

        lane.refresh_from_db()
        self.assertIsNone(lane.channel_id)

    def test_two_active_channels_bind_nothing(self):
        """Alphabetical accident is the bug; there is no non-arbitrary answer here."""
        from apps.orchestration.seeding import bind_lane_channels

        first = self._channel("aaa")
        self._channel("zzz")
        lane = self._lane("delivers", ["notify"])

        bind_lane_channels(PipelineDefinition, type(first))

        lane.refresh_from_db()
        self.assertIsNone(lane.channel_id)

    def test_an_operator_choice_is_never_overwritten(self):
        from apps.orchestration.seeding import bind_lane_channels

        chosen = self._channel("chosen")
        lane = self._lane("delivers", ["notify"], channel=chosen)
        # A second channel would make the seed ambiguous anyway; this proves the
        # guard is "already set", not "only one exists".
        bind_lane_channels(PipelineDefinition, type(chosen))

        lane.refresh_from_db()
        self.assertEqual(lane.channel_id, chosen.id)

    def test_an_inactive_channel_is_not_bound(self):
        from apps.orchestration.seeding import bind_lane_channels

        channel = self._channel(is_active=False)
        lane = self._lane("delivers", ["notify"])

        bind_lane_channels(PipelineDefinition, type(channel))

        lane.refresh_from_db()
        self.assertIsNone(lane.channel_id)
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/orchestration/_tests/test_orchestrator_routing.py -k LaneChannelSeed -v
```

Expected: `ModuleNotFoundError: apps.orchestration.seeding`.

**Step 3: Write the helper**

Create `apps/orchestration/seeding.py`. It takes the model classes as arguments so the migration
can pass its historical models and the tests can pass the real ones:

```python
"""Seed helpers shared by data migrations and their tests.

Migrations must use ``apps.get_model``; tests want the real classes. Passing the
models in keeps one implementation for both instead of a migration body no test
ever executes.
"""


def bind_lane_channels(pipeline_model, channel_model) -> int:
    """Point every delivering lane at the single active channel. Return how many.

    Binds only when exactly one active channel exists: with several there is no
    non-arbitrary answer, and picking by name is the misroute this exists to
    remove. Only fills a NULL channel — a lane an operator has already pointed
    somewhere is never repointed. A lane that does not list ``notify`` never
    delivers and needs no channel.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    if len(channels) != 1:
        return 0

    bound = 0
    for lane in pipeline_model.objects.filter(channel__isnull=True):
        if "notify" not in (lane.stages or []):
            continue
        lane.channel = channels[0]
        lane.save(update_fields=["channel"])
        bound += 1
    return bound
```

Note it reads `lane.stages` directly rather than `routable_stages()`, because a migration's
historical model has no methods.

**Step 4: Write the migration**

`apps/orchestration/migrations/0017_bind_lane_channels.py`, following `0016`'s structure: a module
docstring explaining why the row exists, `get_or_create` for the lanes, then `bind_lane_channels`.
Re-seed all four defaults (`resolved-all-clear`, `cluster-nodes`, `hub-self-check`, `catch-all`)
with the shapes recorded in the design's table — an operator may have deleted them, and this
migration is what makes a wiped table whole again.

`backwards` unbinds nothing and deletes nothing: it is not this migration's business to remove an
operator's channel choice. State that in the docstring rather than leaving an empty function
unexplained.

**Step 5: Apply and test**

```bash
uv run python manage.py migrate && uv run pytest apps/orchestration/_tests/ -q
```

**Step 6: Commit**

```bash
git add apps/orchestration/
git commit -m "feat(orchestration): seed the default lanes and bind their channel"
```

---

## Task 4: A hub that cannot deliver says so

**Files:**
- Modify: `config/dashboard.py` — `build_readiness()`
- Modify: `apps/checkers/preflight/checks.py` — `check_pipeline_state()`
- Test: `apps/orchestration/_tests/test_admin.py` (or wherever `build_readiness` is tested) and
  `apps/checkers/_tests/preflight/test_checks.py`

**Step 1: Write the failing tests**

One per surface. Assert the *finding*, not the wording:

```python
def test_readiness_reports_a_lane_that_cannot_deliver(self):
    from config.dashboard import build_readiness

    PipelineDefinition.objects.create(
        name="mute", match=[], stages=["notify"], priority=1, channel=None
    )

    entry = next(e for e in build_readiness() if e["key"] == "lane_channels")
    assert entry["status"] == "error"

def test_readiness_is_ok_when_every_delivering_lane_has_a_channel(self):
    ...
    assert entry["status"] == "ok"

def test_a_lane_that_never_notifies_is_not_reported(self):
    """hub-self-check lists no stages; it is not broken, it is quiet by design."""
```

Find the existing `build_readiness` tests first and match their setup style.

**Step 2: Run, watch them fail (`StopIteration` — no such key)**

**Step 3: Implement**

In `build_readiness`, after the Channels entry, count lanes where
`"notify" in lane.routable_stages()` and `routed_channel()` is None. Status `error` when any,
`ok` otherwise; `url` points at the pipeline-definition changelist. Use `routed_channel()` — do not
re-derive "active".

In `check_pipeline_state`, add a `warn` for the same condition, with a hint naming the admin page
and saying such a run now fails as `no_channel` rather than delivering elsewhere.

**Step 4: Run**

```bash
uv run pytest apps/checkers/_tests/ apps/orchestration/_tests/ -q
```

**Step 5: Commit**

```bash
git add config/ apps/checkers/
git commit -m "feat(config): readiness and preflight report lanes that cannot deliver"
```

---

## Task 5: Documentation

**Files:**
- Modify: `apps/notify/AGENTS.md` — the selection contract: the default channel is opt-in, and the
  pipeline does not opt in.
- Modify: `apps/orchestration/AGENTS.md` — `no_channel` beside `no_route` in the routing section.

Say plainly that delivery has exactly one source of truth (`routed_channel()`), and that a lane
which routes to NOTIFY without an active channel fails rather than defaulting.

**Verify:**

```bash
uv run pytest -q && uv run black . --check && uv run ruff check . && uv run pip-audit --strict --desc
```

**Commit:**

```bash
git add apps/notify/AGENTS.md apps/orchestration/AGENTS.md
git commit -m "docs: a lane delivers to its own channel or not at all"
```

---

## Deployment note

Hub-only. One migration, no node redeploy. The operator is deleting all `PipelineDefinition` rows
immediately before this merges, so `0017` seeds the full default set and binds the channel in one
step — provided exactly one notification channel is active. With two or more, no lane is bound and
the readiness panel will show `error` until one is chosen; that is deliberate.

{% endraw %}
