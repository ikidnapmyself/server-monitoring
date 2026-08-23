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

**Step 4: Raise both gaps from NotifyExecutor**

Two call sites, one construct. Add to the Task 2 tests first:

```python
    def test_an_unregistered_driver_fails_non_retryably(self):
        """A lane naming a driver that does not exist retries three times today.

        No retry can invent a driver, and "Mark for Retry" in the admin spins it
        again — a pointless loop against a typo in a config field.
        """
        from apps.notify.models import NotificationChannel
        from apps.orchestration.errors import StageExecutionError

        channel = NotificationChannel.objects.create(
            name="ghost", driver="does-not-exist", is_active=True, config={}
        )
        incident = self._incident_on_lane(channel=channel)

        with self.assertRaises(StageExecutionError) as caught:
            NotifyExecutor().execute(_ctx(payload={}, incident_id=incident.id))

        self.assertFalse(caught.exception.retryable)
        self.assertIn("no_driver", "; ".join(caught.exception.errors))
```

Then replace the existing `driver_cls is None` branch (`executors.py:450`), which currently appends
to `result.errors` and returns — becoming a *retryable* failure:

```python
            if driver_cls is None:
                raise routing_gap(
                    PipelineStage.NOTIFY,
                    "no_driver",
                    f"{provider_name!r} is not a registered driver "
                    f"(available: {', '.join(sorted(DRIVER_REGISTRY))})",
                )
```

The available-driver list stays in the message: it is what turns "no_driver" into a fix.

**Step 5: Raise `no_channel`**

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

**Step 6: Run the full suite**

```bash
uv run pytest -q
```

Some existing tests will now fail because they relied on the implicit default. For each: if the
test is about delivery, give its lane or payload a channel — that is the setup a real deployment
has. If the test is about something else and merely tripped over notify, the same fix applies. Do
not weaken an assertion, and do not re-add the fallback to make a test pass.

Also check nothing imports `StageExecutionError` in a way the re-export misses:
`grep -rn "StageExecutionError" apps/`.

**Step 7: Commit**

```bash
git add apps/orchestration/
git commit -m "feat(orchestration): the routing table's three gaps share one failure"
```

---

## Task 3: Seed a routing table that matches how the hub is configured

**Files:**
- Create: `apps/orchestration/seeding.py`
- Create: `apps/orchestration/migrations/0017_seed_routing_table.py`
- Test: `apps/orchestration/_tests/test_seeding.py`

Read design §2.1 first. The rule this task exists to honour: **a channel is optional, a lane that
lists `notify` is not.** An operator who reads the admin daily and runs no Slack is not
misconfigured — they have no lane claiming to deliver. The seed must not manufacture an intent they
never expressed and then fail every run for it.

**Step 1: Write the failing tests**

```python
class SeedRoutingTableTests(TestCase):
    """The seed reads the hub instead of assuming it.

    Zero channels means "this hub records" — every lane keeps routing, none claims
    to deliver, and nothing fails. One channel means "this hub delivers there".
    Two or more is the only case a human has to resolve.
    """

    def _channel(self, name="ops", is_active=True):
        from apps.notify.models import NotificationChannel

        return NotificationChannel.objects.create(
            name=name, driver="generic", is_active=is_active, config={}
        )

    def _seed(self):
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        PipelineDefinition.objects.all().delete()
        return seed_routing_table(PipelineDefinition, NotificationChannel)

    def _lane(self, name):
        from apps.orchestration.models import PipelineDefinition

        return PipelineDefinition.objects.get(name=name)

    # --- zero channels: a recording hub -----------------------------------
    def test_no_channel_seeds_lanes_that_do_not_claim_to_deliver(self):
        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])
        self.assertEqual(self._lane("cluster-nodes").stages, ["analyze"])
        self.assertIsNone(self._lane("catch-all").channel_id)

    def test_no_channel_deactivates_the_resolved_lane(self):
        """Its only purpose is to notify an all-clear; active it would just shadow."""
        self._seed()

        self.assertFalse(self._lane("resolved-all-clear").is_active)

    def test_no_channel_still_routes_everything(self):
        """Recording is not silence: alerts still match, check and analyse."""
        from apps.orchestration.models import PipelineDefinition

        self._seed()

        self.assertTrue(
            PipelineDefinition.objects.filter(is_active=True, match=[]).exists()
        )

    # --- one channel: a delivering hub ------------------------------------
    def test_one_channel_seeds_notify_and_binds_it(self):
        channel = self._channel()

        self._seed()

        lane = self._lane("catch-all")
        self.assertEqual(lane.stages, ["check", "analyze", "notify"])
        self.assertEqual(lane.channel_id, channel.id)
        self.assertTrue(self._lane("resolved-all-clear").is_active)

    def test_an_inactive_channel_does_not_count(self):
        self._channel(is_active=False)

        self._seed()

        self.assertEqual(self._lane("catch-all").stages, ["check", "analyze"])

    # --- two or more: the operator chooses --------------------------------
    def test_several_channels_seed_notify_but_bind_nothing(self):
        """Alphabetical accident is the bug being removed; there is no right answer."""
        self._channel("aaa")
        self._channel("zzz")

        self._seed()

        lane = self._lane("catch-all")
        self.assertIn("notify", lane.stages)
        self.assertIsNone(lane.channel_id)

    # --- idempotence and operator intent ----------------------------------
    def test_an_existing_lane_is_never_rewritten(self):
        """get_or_create on name: an operator's row is theirs."""
        from apps.orchestration.models import PipelineDefinition

        self._channel()
        PipelineDefinition.objects.create(
            name="catch-all", match=[], stages=[], priority=7, is_active=False
        )

        from apps.notify.models import NotificationChannel
        from apps.orchestration.seeding import seed_routing_table

        seed_routing_table(PipelineDefinition, NotificationChannel)

        lane = self._lane("catch-all")
        self.assertEqual(lane.stages, [])
        self.assertEqual(lane.priority, 7)

    def test_seeding_twice_changes_nothing(self):
        self._channel()

        self._seed()
        before = list(
            self._lane("catch-all").__class__.objects.values_list("name", "stages", "channel")
        )
        from apps.notify.models import NotificationChannel
        from apps.orchestration.models import PipelineDefinition
        from apps.orchestration.seeding import seed_routing_table

        seed_routing_table(PipelineDefinition, NotificationChannel)

        after = list(PipelineDefinition.objects.values_list("name", "stages", "channel"))
        self.assertEqual(before, after)

    def test_hub_self_check_never_gets_a_channel(self):
        """It lists no stages, so it never delivers and needs nothing bound."""
        self._channel()

        self._seed()

        self.assertIsNone(self._lane("hub-self-check").channel_id)
```

**Step 2: Run and watch them fail**

```bash
uv run pytest apps/orchestration/_tests/test_seeding.py -v
```

Expected: `ModuleNotFoundError: apps.orchestration.seeding`.

**Step 3: Write the seeding module**

Create `apps/orchestration/seeding.py`. It takes the model classes as arguments so the migration
can pass its historical models and the tests can pass the real ones — one implementation, exercised
by both, instead of a migration body no test ever runs.

```python
"""Seed the routing table to match how this hub is actually configured.

Shared by the data migration and its tests: migrations must use
``apps.get_model``, tests want the real classes, so the models are passed in.

The rule that shapes everything here: **a channel is optional, a lane that lists
``notify`` is not.** ``stages`` is the operator's statement of intent — a hub with
no channel is not broken, it simply has no lane claiming to deliver, and seeding
``notify`` onto it would manufacture an intent it can never satisfy (every run
then failing ``no_channel``). See
docs/plans/2026-08-22-lane-channel-required-design.md §2.1.
"""

_LANES = [
    {
        "name": "resolved-all-clear",
        "description": (
            "Resolved incidents notify without analysis: there is nothing left to "
            "diagnose, and an LLM call on an all-clear is pure cost."
        ),
        "match": [{"field": "status", "op": "is", "value": "resolved"}],
        "stages": ["notify"],
        "priority": 40,
    },
    {
        "name": "cluster-nodes",
        "description": (
            "Alerts pushed by a node. CHECK is omitted: the node already ran its own "
            "checkers, so hub-side checks would report the hub's CPU and disk."
        ),
        "match": [{"field": "source", "op": "is", "value": "cluster"}],
        "stages": ["analyze", "notify"],
        "priority": 50,
    },
    {
        "name": "hub-self-check",
        "description": (
            "The hub's own scheduled checks. Records and correlates only: the cron "
            "repeats every five minutes and a still-firing alert is re-reported each "
            "time."
        ),
        "match": [{"field": "origin", "op": "is", "value": "checker_generated"}],
        "stages": [],
        "priority": 50,
    },
    {
        "name": "catch-all",
        "description": "Everything else. The routing table's last word, as an editable row.",
        "match": [],
        "stages": ["check", "analyze", "notify"],
        "priority": 1000,
    },
]


def seed_routing_table(pipeline_model, channel_model) -> dict:
    """Create the default lanes, shaped by how many channels are active.

    Returns ``{"created": n, "bound": n, "delivering": bool}`` for the caller to
    log. Idempotent: ``get_or_create`` on ``name`` never rewrites an operator's row.
    """
    channels = list(channel_model.objects.filter(is_active=True)[:2])
    delivering = bool(channels)

    created = bound = 0
    for lane in _LANES:
        fields = dict(lane)
        name = fields.pop("name")
        stages = list(fields.pop("stages"))
        if not delivering:
            # Nothing to deliver to: the lane must not claim it will.
            stages = [s for s in stages if s != "notify"]
        # A lane that exists only to notify has nothing left to do.
        is_active = bool(stages) or name == "hub-self-check"

        obj, was_created = pipeline_model.objects.get_or_create(
            name=name, defaults={**fields, "stages": stages, "is_active": is_active}
        )
        created += int(was_created)

        # Bind only when the answer is not arbitrary, and only into an empty slot.
        if len(channels) == 1 and obj.channel_id is None and "notify" in (obj.stages or []):
            obj.channel = channels[0]
            obj.save(update_fields=["channel"])
            bound += 1

    return {"created": created, "bound": bound, "delivering": delivering}
```

Note `hub-self-check` stays active with empty stages — that is its designed shape (record and
correlate), not an accident of this seed.

**Step 4: Write the migration**

`apps/orchestration/migrations/0017_seed_routing_table.py`, following `0016`'s structure. Its
module docstring must say why the seed is channel-aware, not just what it does. Body:

```python
def forwards(apps, schema_editor):
    seed_routing_table(
        apps.get_model("orchestration", "PipelineDefinition"),
        apps.get_model("notify", "NotificationChannel"),
    )
```

`backwards` deletes nothing and unbinds nothing — it is not this migration's business to remove an
operator's lanes or their channel choice. Say that in the docstring rather than leaving an empty
function unexplained.

Add the `notify` app to the migration's `dependencies` (it reads `NotificationChannel`), alongside
`("orchestration", "0016_seed_resolved_lane")`.

**Step 5: Apply and test**

```bash
uv run python manage.py migrate && uv run pytest apps/orchestration/_tests/ -q
```

Note the migration is a no-op on this developer database, where `0012`/`0014`/`0016` already
seeded the lanes — `get_or_create` finds them. That is correct and is what the idempotence test
covers.

**Step 6: Commit**

```bash
git add apps/orchestration/
git commit -m "feat(orchestration): seed a routing table shaped by the hub's channels"
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
def test_a_lane_that_claims_to_deliver_but_cannot_is_an_error(self):
    PipelineDefinition.objects.create(
        name="mute", match=[], stages=["notify"], priority=1, channel=None
    )

    assert self._entry()["status"] == "error"

def test_a_recording_hub_is_not_an_error(self):
    """No channel and no delivering lane is a supported setup, not a fault.

    An operator who reads the admin daily and runs no Slack must not see red.
    """
    PipelineDefinition.objects.create(name="records", match=[], stages=["analyze"], priority=1)

    assert self._entry()["status"] == "info"

def test_every_delivering_lane_bound_is_ok(self):
    ...
    assert self._entry()["status"] == "ok"

def test_a_channel_nobody_delivers_to_is_a_nudge_not_an_alarm(self):
    """One edit away from delivering — say so, do not shout."""
    ...
    entry = self._entry()
    assert entry["status"] == "ok"
    assert "no lane" in entry["detail"].lower()

def test_a_lane_that_never_notifies_is_not_reported(self):
    """hub-self-check lists no stages; it is not broken, it is quiet by design."""
```

Find the existing `build_readiness` tests first and match their setup style.

**Step 2: Run, watch them fail (`StopIteration` — no such key)**

**Step 3: Implement**

In `build_readiness`, after the Channels entry, add a `lane_channels` entry with three states —
see design §2.3:

- **`error`** — at least one lane lists `notify` and `routed_channel()` is None. A row claims to
  deliver and cannot.
- **`info`** — no lane delivers AND no channel is active. "Recording only": a supported way to run
  this hub, not a fault. Must not read as a problem.
- **`ok`** — every delivering lane has an active channel. When a channel exists but no lane points
  at it, keep `ok` and say so in `detail` ("no lane delivers to it yet") — a nudge, not an alarm.

Use `routed_channel()` and `routable_stages()`; do not re-derive "active" or parse `stages`.

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
