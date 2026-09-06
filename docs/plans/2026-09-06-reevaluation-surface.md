---
title: "Re-evaluation as a surface — implementation"
parent: Plans
---

{% raw %}

# Re-evaluation as a Surface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make hub-side re-evaluation visible and actionable — one page showing what
every policy is doing, a per-alert re-evaluate action that explains its refusals, and
an applied re-evaluation that announces itself.

**Architecture:** Three layers, in order. First the scorer contract in
`apps/alerts/reevaluation.py` changes from `tuple | None` to `Verdict | Skip`, so nine
distinct failures stop being one silence — ingest behaviour is unchanged, every `Skip`
is a passthrough. Then `apps/alerts/reeval_existing.py` gains a `ReevalScope` so preview
and apply work on a node, a node-and-checker, or one alert, and an applied change
enqueues a MANUAL pipeline run per changed incident. Then the admin surfaces:
`/admin/policy/` widens into the re-evaluation page, and `AlertAdmin` gains the action
and an audit panel.

**Tech Stack:** Django 5, `django_object_actions`, pytest + pytest-django, `uv`.

**Design:** `docs/plans/2026-09-06-reevaluation-surface-design.md`

**Branch:** work on `feat/policy-overview-page` (this plan reshapes the page added
there, which is not yet merged). Do not use a git worktree.

---

## Ground rules for the engineer

- **Absolute imports always.** `from apps.alerts.models import Alert`, never relative.
- **Line length 100.** Black and Ruff are configured in `pyproject.toml`.
- **Never add explanatory comments to justify a diff.** Comment only a non-obvious
  constraint or a load-bearing invariant. The existing modules are a good model.
- **Tests are `django.test.TestCase` with unittest asserts**, matching
  `apps/alerts/_tests/test_reevaluation.py`. Run them with `uv run pytest`.
- **Fail-open is the one rule that cannot break.** `reevaluate_severity` runs inside
  `AlertOrchestrator._process_alert`, which is inside the webhook's atomic block. An
  exception there rolls back an entire batch of alerts. Every task that touches
  `reevaluation.py` must keep `test_reevaluation.py` green.
- **100% branch coverage on changed lines.** Verify at the end with
  `uv run coverage run -m pytest && uv run coverage report`.
- Commit after every task. Pre-commit runs the full test suite, so commits take a
  couple of minutes. That is expected.
- **`SkipReason` is `str, Enum`, not `StrEnum`** (Python 3.10). An f-string is fine:
  `__format__` comes from the `str` mixin, so `f"{SkipReason.NO_METRICS}"` gives
  `"no_metrics"`. But `str(member)`, `"%s" % member` and a Django template `{{ member }}`
  all give `"SkipReason.NO_METRICS"`. Say `.value` in those three places.
- **Never use `dataclasses.replace` on a `Skip`.** Its constructor collects keyword
  arguments into `context`, so `replace(skip, context={...})` nests one level and
  silently produces the wrong object. Build a new `Skip` instead.

---

## Phase 1 — the scorer contract

This phase lands alone. Nothing user-visible changes; the point is that the scorers
start saying *why*.

### Task 1: Outcome types

**Files:**
- Modify: `apps/alerts/reevaluation.py`
- Test: `apps/alerts/_tests/test_reevaluation.py`

**Step 1: Write the failing test**

Append to `apps/alerts/_tests/test_reevaluation.py`:

```python
class OutcomeTypeTests(TestCase):
    def test_verdict_carries_the_score(self):
        from apps.alerts.reevaluation import Verdict

        verdict = Verdict(severity="warning", status="firing", value=91.3)
        self.assertEqual(verdict.severity, "warning")
        self.assertEqual(verdict.status, "firing")
        self.assertEqual(verdict.value, 91.3)

    def test_skip_carries_a_reason_and_context(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        skip = Skip(SkipReason.NO_METRICS)
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)
        self.assertEqual(skip.context, {})

    def test_skip_context_is_keyword_only(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        skip = Skip(SkipReason.UNCHANGED, value=41.2, warning=99.0)
        self.assertEqual(skip.context["value"], 41.2)
        self.assertEqual(skip.context["warning"], 99.0)
```

**Step 2: Run it to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py::OutcomeTypeTests -v`
Expected: FAIL with `ImportError: cannot import name 'Verdict'`

**Step 3: Write the minimal implementation**

In `apps/alerts/reevaluation.py`, after the imports and before `PRIMARY_METRIC`:

```python
class SkipReason(str, Enum):
    """Why a re-evaluation produced no verdict.

    Every value is a passthrough at ingest. They differ only where a human asked
    the question and is owed an answer.
    """

    NO_SCORER = "no_scorer"
    NO_POLICY = "no_policy"
    MALFORMED_POLICY = "malformed_policy"
    INCOMPLETE_THRESHOLDS = "incomplete_thresholds"
    INVERTED_THRESHOLDS = "inverted_thresholds"
    NO_PRIMARY_METRIC = "no_primary_metric"
    NO_METRICS = "no_metrics"
    NO_METRIC_VALUE = "no_metric_value"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class Verdict:
    """A score the caller may act on."""

    severity: str
    status: str
    value: float


@dataclass(frozen=True)
class Skip:
    """No score, and why. ``context`` carries what the sentence needs."""

    reason: SkipReason
    context: dict

    def __init__(self, reason: SkipReason, **context):
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "context", context)


Outcome = Verdict | Skip
```

Add to the imports at the top of the module:

```python
from dataclasses import dataclass
from enum import Enum
```

`StrEnum` is 3.11+ and this project targets 3.10. `str, Enum` matches the existing
precedent at `apps/alerts/diagnosis.py:17`.

Add `"Verdict"`, `"Skip"`, `"SkipReason"` to `__all__`.

**Step 4: Run the tests**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py -v`
Expected: PASS, including every pre-existing test.

**Step 5: Commit**

```bash
git add apps/alerts/reevaluation.py apps/alerts/_tests/test_reevaluation.py
git commit -m "feat(alerts): add the re-evaluation outcome types"
```

---

### Task 2: `_score_numeric` returns an Outcome

**Files:**
- Modify: `apps/alerts/reevaluation.py:49-72` (`_score_numeric`), `:138-144`
  (`numeric_evaluator`), `:193-197` (`_reevaluate`)
- Modify: `apps/alerts/reeval_existing.py:44-54` (`_score_alert`) — minimal adapter only
- Test: `apps/alerts/_tests/test_reevaluation.py`

**Step 1: Write the failing test**

```python
class ScoreNumericReasonTests(TestCase):
    def _skip(self, cfg, metrics=None, checker="cpu"):
        from apps.alerts.reevaluation import _score_numeric

        return _score_numeric(checker, metrics if metrics is not None else {}, cfg)

    def test_missing_policy_says_so(self):
        from apps.alerts.reevaluation import SkipReason

        self.assertEqual(self._skip(None).reason, SkipReason.NO_POLICY)

    def test_non_mapping_policy_is_malformed(self):
        from apps.alerts.reevaluation import SkipReason

        self.assertEqual(self._skip("99").reason, SkipReason.MALFORMED_POLICY)

    def test_half_filled_thresholds_are_incomplete(self):
        from apps.alerts.reevaluation import SkipReason

        skip = self._skip({"warning_threshold": 90})
        self.assertEqual(skip.reason, SkipReason.INCOMPLETE_THRESHOLDS)

    def test_inverted_thresholds_say_so(self):
        from apps.alerts.reevaluation import SkipReason

        skip = self._skip({"warning_threshold": 90, "critical_threshold": 80})
        self.assertEqual(skip.reason, SkipReason.INVERTED_THRESHOLDS)
        self.assertEqual(skip.context["warning"], 90.0)
        self.assertEqual(skip.context["critical"], 80.0)

    def test_unknown_checker_has_no_primary_metric(self):
        from apps.alerts.reevaluation import SkipReason

        skip = self._skip(
            {"warning_threshold": 1, "critical_threshold": 2}, checker="raid"
        )
        self.assertEqual(skip.reason, SkipReason.NO_PRIMARY_METRIC)

    def test_non_mapping_metrics_are_missing(self):
        from apps.alerts.reevaluation import SkipReason

        skip = self._skip({"warning_threshold": 1, "critical_threshold": 2}, metrics="x")
        self.assertEqual(skip.reason, SkipReason.NO_METRICS)

    def test_absent_metric_value_says_which_key(self):
        from apps.alerts.reevaluation import SkipReason

        skip = self._skip({"warning_threshold": 1, "critical_threshold": 2}, metrics={})
        self.assertEqual(skip.reason, SkipReason.NO_METRIC_VALUE)
        self.assertEqual(skip.context["metric_key"], "cpu_percent")

    def test_a_score_is_a_verdict_carrying_the_thresholds(self):
        from apps.alerts.reevaluation import Verdict

        outcome = self._skip(
            {"warning_threshold": 90, "critical_threshold": 95},
            metrics={"cpu_percent": 91.5},
        )
        self.assertIsInstance(outcome, Verdict)
        self.assertEqual(outcome.severity, "warning")
        self.assertEqual(outcome.value, 91.5)
```

**Step 2: Run it to verify it fails**

Run: `uv run pytest apps/alerts/_tests/test_reevaluation.py::ScoreNumericReasonTests -v`
Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'reason'`

**Step 3: Rewrite `_score_numeric`**

```python
def _score_numeric(checker: str, metrics: dict, cfg) -> Outcome:
    """Pure scorer shared by ingest and config-change re-evaluation.

    `_number` rejects bool (a subclass of int) and non-numbers. An inverted config
    (critical below warning) is malformed. Every failure is a ``Skip``, which every
    caller treats as passthrough, so this stays fail-open.
    """
    if cfg is None:
        return Skip(SkipReason.NO_POLICY, checker=checker)
    if not isinstance(cfg, dict):
        return Skip(SkipReason.MALFORMED_POLICY, checker=checker)
    warn = _number(cfg.get("warning_threshold"))
    crit = _number(cfg.get("critical_threshold"))
    if warn is None or crit is None:
        return Skip(SkipReason.INCOMPLETE_THRESHOLDS, checker=checker)
    if crit < warn:
        return Skip(SkipReason.INVERTED_THRESHOLDS, warning=warn, critical=crit)
    metric_key = PRIMARY_METRIC.get(checker)
    if metric_key is None:
        return Skip(SkipReason.NO_PRIMARY_METRIC, checker=checker)
    if not isinstance(metrics, dict):
        return Skip(SkipReason.NO_METRICS, checker=checker)
    value = _number(metrics.get(metric_key))
    if value is None:
        return Skip(SkipReason.NO_METRIC_VALUE, metric_key=metric_key)
    if value >= crit:
        return Verdict("critical", "firing", value)
    if value >= warn:
        return Verdict("warning", "firing", value)
    return Verdict("info", "resolved", value)
```

**Step 4: Adapt the two callers so nothing else changes yet**

`numeric_evaluator` (`:138`):

```python
def numeric_evaluator(parsed: ParsedAlert, cfg: dict) -> Outcome:
    """Score a numeric checker from the alert's stored metrics."""
    metrics = _metrics(parsed)
    checker = (parsed.labels or {}).get("checker", "")
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker=checker)
    return _score_numeric(checker, metrics, cfg)
```

In `_reevaluate` (`:193`), replace the `outcome is None` branch:

```python
    outcome = evaluator(parsed, cfg)
    if not isinstance(outcome, Verdict):
        return parsed

    severity, status, value = outcome.severity, outcome.status, outcome.value
```

In `apps/alerts/reeval_existing.py`, `_score_alert` keeps its `tuple | None` return for
now, so this task changes one behaviour at a time:

```python
def _score_alert(alert: Alert, config: dict) -> tuple[str, str, float] | None:
    checker = (alert.labels or {}).get("checker", "")
    scorer = SCORERS.get(checker)
    if scorer is None:
        return None
    cfg = (config or {}).get(checker)
    metrics = parse_metrics(alert.annotations)
    if metrics is None:
        return None
    outcome = scorer(checker, metrics, cfg)
    if not isinstance(outcome, Verdict):
        return None
    return (outcome.severity, outcome.status, outcome.value)
```

Import `Verdict` there.

**Step 5: Run the whole alerts suite**

Run: `uv run pytest apps/alerts/ -v`
Expected: PASS. If any pre-existing test asserted `_score_numeric(...) is None`, update
it to assert the `Skip` reason — that is the point of the change, not a regression.

**Step 6: Commit**

```bash
git add apps/alerts/reevaluation.py apps/alerts/reeval_existing.py apps/alerts/_tests/
git commit -m "feat(alerts): make the numeric scorer say why it skipped"
```

---

### Task 3: `_score_allowlist` returns an Outcome

**Files:**
- Modify: `apps/alerts/reevaluation.py:111-152`
- Test: `apps/alerts/_tests/test_reevaluation.py`

Same shape as Task 2. Write one test per branch first, then rewrite:

```python
def _score_allowlist(checker: str, metrics: dict, cfg) -> Outcome:
    """Re-flag listening ports against a per-node allowlist. Binary warning/ok.

    Reuses the checker's own flagging semantics against the full ``listening``
    inventory the node reports. ``checker`` is unused (uniform scorer signature).
    """
    if cfg is None:
        return Skip(SkipReason.NO_POLICY, checker="listening_ports")
    if not isinstance(cfg, dict):
        return Skip(SkipReason.MALFORMED_POLICY, checker="listening_ports")
    if not isinstance(metrics, dict):
        return Skip(SkipReason.NO_METRICS, checker="listening_ports")
    allow = cfg.get("allowlist")
    if not isinstance(allow, list):
        return Skip(SkipReason.MALFORMED_POLICY, checker="listening_ports")
    allowset = _int_set(allow)
    if allowset is None:
        return Skip(SkipReason.MALFORMED_POLICY, checker="listening_ports")
    listening = metrics.get("listening")
    if not isinstance(listening, list):
        return Skip(SkipReason.NO_METRIC_VALUE, metric_key="listening")
    flagged = _flag_ports(listening, allowset)
    if flagged is None:
        return Skip(SkipReason.NO_METRIC_VALUE, metric_key="listening")
    count = float(len(flagged))
    if flagged:
        return Verdict("warning", "firing", count)
    return Verdict("info", "resolved", count)
```

And `allowlist_evaluator`:

```python
def allowlist_evaluator(parsed: ParsedAlert, cfg: dict) -> Outcome:
    """Score listening_ports from the alert's stored inventory."""
    metrics = _metrics(parsed)
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker="listening_ports")
    return _score_allowlist("listening_ports", metrics, cfg)
```

Update the `SCORERS` and `REEVALUATORS` type annotations to return `Outcome`, and
correct their docstring comments, which currently say `| None`.

**Delete the transitional tuple branches Task 2 added.** Task 2 could not use a plain
`isinstance(outcome, Verdict)` check, because `_score_allowlist` still returned a bare
tuple, so both `_reevaluate` and `reeval_existing._score_alert` currently accept either
shape behind a one-line comment. Once this task lands, no scorer returns a tuple, so
both branches and both comments must go. Grep for `isinstance(outcome, tuple)` to be
sure none is left: a dead branch here is one mypy cannot flag and no test will reach.

The `listening_ports` path through `_score_alert` is already covered, by
`test_reeval_existing.py:150` and `:172`. Both drive `apply_node_alert_reeval` end to
end through the tuple branch, so they are what proves the branch is safe to delete. Run
them before and after.

**Also clean up three things Task 2's review flagged in `test_reevaluation.py`:**
rename `test_a_score_is_a_verdict_carrying_the_thresholds` (a `Verdict` carries no
thresholds) and assert its `status` too; drop the eight function-local imports in
`ScoreNumericReasonTests`, which re-import names the file already imports at module
level; and rename the `_skip` helper, which is used for the verdict case as well.

Run: `uv run pytest apps/alerts/ -v` → PASS

```bash
git commit -am "feat(alerts): make the allowlist scorer say why it skipped"
```

---

### Task 4: One sentence per skip reason

**Files:**
- Modify: `apps/alerts/reevaluation.py`
- Test: `apps/alerts/_tests/test_reevaluation.py`

This is what an operator actually reads, so the test asserts the sentence.

**Step 1: Write the failing test**

```python
class DescribeSkipTests(TestCase):
    def _say(self, skip, checker="cpu", instance_id="fiyat-ekrani"):
        from apps.alerts.reevaluation import describe_skip

        return describe_skip(skip, checker=checker, instance_id=instance_id)

    def test_no_scorer_names_the_checker(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        self.assertEqual(
            self._say(Skip(SkipReason.NO_SCORER), checker="disk_temp"),
            "disk_temp is not re-evaluatable — no scorer knows it.",
        )

    def test_no_policy_names_the_node(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        self.assertEqual(
            self._say(Skip(SkipReason.NO_POLICY)),
            "No policy set for cpu on fiyat-ekrani.",
        )

    def test_no_metrics_explains_there_is_nothing_to_score(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        self.assertEqual(
            self._say(Skip(SkipReason.NO_METRICS)),
            "This alert carries no metrics, so there is nothing to re-score.",
        )

    def test_unchanged_prints_the_value_and_the_threshold(self):
        from apps.alerts.reevaluation import Skip, SkipReason

        self.assertEqual(
            self._say(Skip(SkipReason.UNCHANGED, value=41.2, warning=99.0)),
            "Policy already matches: cpu is at 41.2, warning starts at 99.0.",
        )

    def test_every_reason_has_a_sentence(self):
        from apps.alerts.reevaluation import SkipReason, describe_skip, Skip

        for reason in SkipReason:
            sentence = describe_skip(
                Skip(reason, value=1.0, warning=2.0, critical=3.0, metric_key="k"),
                checker="cpu",
                instance_id="n1",
            )
            self.assertTrue(sentence.endswith("."), reason)
```

That last test is the completeness guard, in the same spirit as the `SECTION_MAP` one.

**Step 2: Run it**

Expected: FAIL with `ImportError: cannot import name 'describe_skip'`

**Step 3: Implement**

Add a `_SKIP_SENTENCES: dict[SkipReason, str]` of format strings and:

```python
def describe_skip(skip: Skip, *, checker: str, instance_id: str) -> str:
    """One sentence saying why this alert was not re-scored.

    The scorers cannot know the node, so the caller supplies it. Every reason has
    a sentence; a reason added without one raises here rather than printing a
    blank cell.
    """
    template = _SKIP_SENTENCES[skip.reason]
    return template.format(checker=checker, instance_id=instance_id, **skip.context)
```

Write the sentences to match the tests above. Use an em-dash-free style consistent
with the rest of the codebase, except in `NO_SCORER` where the test fixes the wording.

Two things Task 3 left for you to resolve in the wording:

`NO_METRIC_VALUE` covers two different situations for `listening_ports`: the inventory
is not a list, and one entry in it is malformed. "No value for listening" describes the
first well and the second loosely. Either widen the sentence to cover both honestly or
give the malformed entry its own reason, but do not print a sentence that is false.

`NO_METRICS` is likewise reached from two places: the metrics annotation would not
parse (from the evaluators) and the metrics object is not a mapping (from the scorers).
For an operator these are the same fact, so one sentence is probably right. Decide
deliberately rather than by accident.

**Step 4: Run** `uv run pytest apps/alerts/_tests/test_reevaluation.py -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(alerts): give every skip reason a sentence"
```

---

## Phase 2 — scoping and announcing

### Task 5: `ReevalScope`

**Files:**
- Modify: `apps/alerts/reeval_existing.py`
- Test: `apps/alerts/_tests/test_reeval_existing.py`

**Step 1: Write the failing test**

```python
class ReevalScopeTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(instance_id="web-03", config={})

    def _alert(self, checker, status="firing"):
        return Alert.objects.create(
            fingerprint=f"{checker}-web-03",
            source="cluster",
            name=f"{checker} high",
            severity="critical",
            status=status,
            started_at=timezone.now(),
            node=self.node,
            labels={"checker": checker, "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )

    def test_node_scope_covers_every_firing_alert(self):
        from apps.alerts.reeval_existing import ReevalScope

        self._alert("cpu")
        self._alert("memory")
        self._alert("disk", status="resolved")
        scope = ReevalScope.for_node(self.node)
        self.assertEqual(scope.alerts.count(), 2)

    def test_checker_scope_narrows_to_one_checker(self):
        from apps.alerts.reeval_existing import ReevalScope

        self._alert("cpu")
        self._alert("memory")
        scope = ReevalScope.for_checker(self.node, "cpu")
        self.assertEqual([a.labels["checker"] for a in scope.alerts], ["cpu"])

    def test_alert_scope_is_exactly_one_alert(self):
        from apps.alerts.reeval_existing import ReevalScope

        alert = self._alert("cpu")
        self._alert("memory")
        scope = ReevalScope.for_alert(alert)
        self.assertEqual([a.pk for a in scope.alerts], [alert.pk])
        self.assertEqual(scope.node, self.node)

    def test_alert_scope_finds_the_node_by_label_not_fk(self):
        from apps.alerts.reeval_existing import ReevalScope

        alert = self._alert("cpu")
        alert.node = None
        alert.save(update_fields=["node"])
        scope = ReevalScope.for_alert(alert)
        self.assertEqual(scope.node, self.node)

    def test_the_checker_scopes_sum_to_the_node_scope(self):
        from apps.alerts.reeval_existing import ReevalScope

        self._alert("cpu")
        self._alert("memory")
        node_pks = {a.pk for a in ReevalScope.for_node(self.node).alerts}
        summed = set()
        for checker in ("cpu", "memory"):
            summed |= {a.pk for a in ReevalScope.for_checker(self.node, checker).alerts}
        self.assertEqual(node_pks, summed)
```

**Step 2: Run it** → FAIL, `cannot import name 'ReevalScope'`

**Step 3: Implement**

```python
@dataclass(frozen=True)
class ReevalScope:
    """Which alerts one re-evaluation covers, and whose policy scores them.

    Alerts are matched by their ``instance_id`` label rather than the ``node`` FK:
    the FK is stamped only at alert creation (``resolve_node``), so an alert created
    before its node registered is unlinked yet still belongs to the node.
    """

    node: Node
    alerts: models.QuerySet

    @classmethod
    def for_node(cls, node: Node) -> "ReevalScope":
        return cls(node=node, alerts=cls._open(node))

    @classmethod
    def for_checker(cls, node: Node, checker: str) -> "ReevalScope":
        return cls(node=node, alerts=cls._open(node).filter(labels__checker=checker))

    @classmethod
    def for_alert(cls, alert: Alert) -> "ReevalScope":
        instance_id = (alert.labels or {}).get("instance_id", "")
        node = Node.objects.filter(instance_id=instance_id).first()
        return cls(node=node, alerts=Alert.objects.filter(pk=alert.pk))

    @staticmethod
    def _open(node: Node) -> models.QuerySet:
        return Alert.objects.filter(labels__instance_id=node.instance_id, status="firing")
```

Import `from django.db import models` and `from dataclasses import dataclass`.

Note `for_alert` may find no node. A later task handles that as its own refusal; do
not raise here.

**Step 4: Run** `uv run pytest apps/alerts/_tests/test_reeval_existing.py -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(alerts): scope a re-evaluation to a node, checker, or alert"
```

---

### Task 6: `preview_reeval` / `apply_reeval`, with skips

**Files:**
- Modify: `apps/alerts/reeval_existing.py`
- Test: `apps/alerts/_tests/test_reeval_existing.py`

**Step 1: Write the failing test**

```python
class ReportSkipTests(TestCase):
    def test_a_no_op_preview_explains_each_alert(self):
        from apps.alerts.reeval_existing import ReevalScope, preview_reeval

        node = Node.objects.create(instance_id="web-03", config={})
        Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.0})},
        )
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.changes, [])
        self.assertEqual(len(report.skips), 1)
        self.assertIn("No policy set for cpu on web-03.", report.skips[0].sentence)

    def test_an_unchanged_score_is_a_skip_not_a_silence(self):
        from apps.alerts.reeval_existing import ReevalScope, preview_reeval

        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="info",
            status="resolved",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 41.2})},
        )
        # a resolved alert is out of scope; make it firing to reach UNCHANGED
        Alert.objects.update(status="firing", severity="info")
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertEqual(report.changes, [])
        self.assertIn("Policy already matches", report.skips[0].sentence)

    def test_a_checker_with_no_scorer_is_a_skip(self):
        from apps.alerts.reeval_existing import ReevalScope, preview_reeval

        node = Node.objects.create(instance_id="web-03", config={})
        Alert.objects.create(
            fingerprint="raid-web-03",
            source="cluster",
            name="raid degraded",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            labels={"checker": "raid", "instance_id": "web-03"},
            annotations={},
        )
        report = preview_reeval(ReevalScope.for_node(node))
        self.assertIn("not re-evaluatable", report.skips[0].sentence)
```

**Step 2: Run it** → FAIL

**Step 3: Implement**

Add an `AlertSkip` dataclass (`alert`, `reason`, `sentence`), give `ReevalReport` a
`skips: list[AlertSkip]` and a `node` that may be `None`, then:

```python
def _outcome_for(alert: Alert, config: dict) -> Outcome:
    """Score one alert, or say why it cannot be scored."""
    checker = (alert.labels or {}).get("checker", "")
    scorer = SCORERS.get(checker)
    if scorer is None:
        return Skip(SkipReason.NO_SCORER, checker=checker)
    metrics = parse_metrics(alert.annotations)
    if metrics is None:
        return Skip(SkipReason.NO_METRICS, checker=checker)
    return scorer(checker, metrics, (config or {}).get(checker))


def preview_reeval(scope: ReevalScope) -> ReevalReport:
    """Report which alerts in ``scope`` would change, and why the rest would not."""
    report = ReevalReport(node=scope.node)
    config = scope.node.config if scope.node else {}
    instance_id = scope.node.instance_id if scope.node else ""
    for alert in scope.alerts:
        checker = (alert.labels or {}).get("checker", "")
        outcome = _outcome_for(alert, config)
        if isinstance(outcome, Verdict) and (
            outcome.severity != alert.severity or outcome.status != alert.status
        ):
            report.changes.append(
                AlertChange(
                    alert=alert,
                    old_severity=alert.severity,
                    old_status=alert.status,
                    new_severity=outcome.severity,
                    new_status=outcome.status,
                    value=outcome.value,
                )
            )
            continue
        if isinstance(outcome, Verdict):
            cfg = (config or {}).get(checker) or {}
            outcome = Skip(
                SkipReason.UNCHANGED,
                value=outcome.value,
                warning=cfg.get("warning_threshold"),
            )
        report.skips.append(
            AlertSkip(
                alert=alert,
                reason=outcome.reason,
                sentence=describe_skip(
                    outcome, checker=checker, instance_id=instance_id
                ),
            )
        )
    return report
```

Then rename the body of `apply_node_alert_reeval` to `apply_reeval(scope)`, taking its
report from `preview_reeval(scope)`, and make the two public node functions wrappers:

```python
def preview_node_alert_reeval(node: Node) -> ReevalReport:
    """Every open alert on ``node``; kept for the Node admin button and the command."""
    return preview_reeval(ReevalScope.for_node(node))


def apply_node_alert_reeval(node: Node) -> ReevalReport:
    return apply_reeval(ReevalScope.for_node(node))
```

`_resolve_incidents_for` still takes the node. `scope.node` is `Node | None`, and that
None is reachable from real data: an alert whose `instance_id` label names a node that
was deleted or never registered. Write an explicit refusal branch. Do NOT reach for a
`cast` or a `# type: ignore`.

**Annotate `resolve_node` first.** `apps/alerts/services.py:37` has no return type, so
mypy infers `Any` and will not flag `scope.node.config` for you. Add `-> "Node | None"`
so the type checker enforces the refusal rather than leaving it to memory. That is a
one-line change and it is the reason the honest annotation on `ReevalScope.node` was
worth having.

`ReevalScope.checker` records what the operator asked for. The audit annotation should
say that, not just what was found: a checker scope matching nothing is otherwise
indistinguishable from a node scope on a quiet node.

**Step 4: Run** `uv run pytest apps/alerts/ -v` → PASS, including the existing
`ReevalExistingTests`, which must not need edits.

**Step 5: Commit**

```bash
git commit -am "feat(alerts): report why a re-evaluation changed nothing"
```

---

### Task 7: An applied re-evaluation is distinguishable in history

**Files:**
- Modify: `apps/alerts/reeval_existing.py` (the `AlertHistory.objects.create` call)
- Test: `apps/alerts/_tests/test_reeval_existing.py`

Today a re-evaluation that resolves an alert writes `event="resolved"`, which an
operator's own resolve also writes. The Applied column cannot tell them apart.

**Step 1: Write the failing test**

```python
def test_history_marks_the_re_evaluation(self):
    node = self._node({"cpu": {"warning_threshold": 99, "critical_threshold": 99}})
    alert = self._alert(node, "cpu", 95.2)
    apply_node_alert_reeval(node)
    history = AlertHistory.objects.get(alert=alert)
    self.assertEqual(history.event, "resolved")
    self.assertEqual(history.details["by"], "reeval:config-change")
```

**Step 2: Run it** → FAIL with `KeyError: 'by'`

**Step 3: Implement** — add `"by": "reeval:config-change"` to the `details` dict in the
`AlertHistory.objects.create` call. Rows written before this key existed simply lack it
and read as unknown, which is correct.

**Step 4: Run** → PASS

**Step 5: Commit**

```bash
git commit -am "feat(alerts): mark re-evaluation history apart from an operator resolve"
```

---

### Task 8: Applying announces

**Files:**
- Modify: `apps/alerts/services.py:685-698` (lift `_announce`)
- Modify: `apps/alerts/reeval_existing.py` (`apply_reeval`)
- Test: `apps/alerts/_tests/test_reeval_existing.py`, `apps/alerts/_tests/test_services.py`

**Step 1: Write the failing test**

```python
class ReevalAnnounceTests(TestCase):
    def test_a_resolving_apply_enqueues_one_run_for_the_incident(self):
        from apps.orchestration.models import PipelineOrigin, PipelineRun

        node = Node.objects.create(
            instance_id="web-03",
            config={"cpu": {"warning_threshold": 99, "critical_threshold": 99}},
        )
        incident = Incident.objects.create(title="cpu high", severity="critical")
        alert = Alert.objects.create(
            fingerprint="cpu-web-03",
            source="cluster",
            name="cpu high",
            severity="critical",
            status="firing",
            started_at=timezone.now(),
            node=node,
            incident=incident,
            labels={"checker": "cpu", "instance_id": "web-03"},
            annotations={"metrics": json.dumps({"cpu_percent": 95.2})},
        )
        apply_node_alert_reeval(node)
        runs = PipelineRun.objects.filter(payload__downstream_incident_id=incident.id)
        self.assertEqual(runs.count(), 1)
        self.assertEqual(runs.first().origin, PipelineOrigin.MANUAL)

    def test_a_no_op_apply_enqueues_nothing(self):
        from apps.orchestration.models import PipelineRun

        node = Node.objects.create(instance_id="web-03", config={})
        apply_node_alert_reeval(node)
        self.assertEqual(PipelineRun.objects.count(), 0)
```

**Step 2: Run it** → FAIL, 0 runs

**Step 3: Implement**

In `apps/alerts/services.py`, lift the body of `IncidentManager._announce` to a
module-level function and have the staticmethod call it:

```python
def announce_incident_change(incident: Incident) -> None:
    """Something changed this incident: one inbox run, same as when a node does.

    Shared by operator transitions and by an applied re-evaluation, so the same end
    state reached two ways produces the same pipeline run.
    """
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

In `apply_reeval`, after the change loop and the incident sweep, still inside the
`transaction.atomic` block so the runs commit with the writes:

```python
    for incident in _changed_incidents(report):
        announce_incident_change(incident)
```

`_changed_incidents` collects the distinct incidents of `report.changes`, refreshed from
the database so a status set by `_resolve_incidents_for` is seen. Deduplicate by pk and
skip `None`.

Import inside the function, not at module level: `apps.orchestration` imports
`apps.alerts`, so `apps.alerts` must not import it at module scope.

**Step 4: Run** `uv run pytest apps/alerts/ apps/orchestration/ -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(alerts): announce an applied re-evaluation like an operator transition"
```

---

### Task 9: The confirm page states its consequences

**Files:**
- Modify: `templates/admin/alerts/node/reevaluate_confirm.html`
- Modify: `apps/alerts/admin.py:810-838`
- Test: `apps/alerts/_tests/test_node_admin.py`

The page must show, before the operator presses Confirm: the number of pipeline runs
this will create, each alert's `received_at` beside its value, and the skips.

**Step 1: Write the failing test** asserting the rendered page contains the run count
sentence and a skip sentence.

**Step 2: Run it** → FAIL

**Step 3: Implement**

- Add a `run_count` property to `ReevalReport`: the number of distinct incidents its
  changes touch.
- Add a Value column note and a `Reported` column rendering `change.alert.received_at`.
- **Round the printed value.** `Verdict.value` is a raw float, so a CPU reading can
  render as `41.199999999999996`. Decide the precision once, here, and use it wherever
  a value or a threshold is printed: this template, the skip sentences, the Alert
  page's audit panel, and the overview page's columns.
- Add a skips table below the changes table, headed so it reads as an explanation
  rather than a second list of actions.
- Replace the bare `self.message_user(request, "No open alerts need re-evaluation.")`
  early return: when there are skips, render the same template with an empty changes
  table so the operator is told why. Keep the message only when the scope is genuinely
  empty.
- Add the sentence: `This will create N pipeline run(s) and notify on them.`

**Step 4: Run** `uv run pytest apps/alerts/_tests/test_node_admin.py -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(admin): say what a re-evaluation will do before confirming it"
```

---

## Phase 3 — the surfaces

### Task 10: Re-evaluate one alert

**Files:**
- Modify: `apps/alerts/admin.py:114` (`AlertAdmin`)
- Test: `apps/alerts/_tests/test_alert_admin.py` (create if absent)

**Step 1: Write the failing tests** — the action appears on the change page; a GET
previews without writing; a POST with `confirm` applies; an alert whose checker has no
scorer renders its sentence and offers no Confirm button; an alert whose node has no
policy renders the sentence and a link to that checker's boxes.

**Step 2: Run** → FAIL

**Step 3: Implement**

Make `AlertAdmin` inherit `DjangoObjectActions` (it currently does not; `IncidentAdmin`
at `:277` is the model to follow), add `change_actions = ["reevaluate"]`, and:

```python
@object_action(
    label="Re-evaluate",
    description="Re-score this alert against its node's current policy",
)
def reevaluate(self, request, obj):
    """Preview (then, on POST confirm) re-evaluate this one alert."""
    if not self.has_change_permission(request, obj):
        raise PermissionDenied
    scope = ReevalScope.for_alert(obj)
    report = preview_reeval(scope)
    if request.method == "POST" and request.POST.get("confirm") and report.changes:
        apply_reeval(scope)
        self.message_user(request, "Re-evaluated.")
        return
    return TemplateResponse(...)
```

Reuse the node confirm template rather than writing a second one; it already renders
changes, skips and the run count. Give it a `back_url` so Cancel returns to the alert.

Note the `AlertAdmin` change form template: adding `DjangoObjectActions` needs the
`django_object_actions` change form, the same requirement `NodeAdmin` documents. If
`AlertAdmin` has its own `change_form.html`, it must extend
`django_object_actions/change_form.html` or the button disappears.

**Step 4: Run** `uv run pytest apps/alerts/ -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(admin): re-evaluate a single alert from its own page"
```

---

### Task 11: The alert says whether a re-evaluation applied

**Files:**
- Modify: `apps/alerts/admin.py` (`AlertAdmin` fieldsets + a readonly display)
- Modify: `apps/alerts/timeline.py`
- Test: `apps/alerts/_tests/test_alert_admin.py`, `apps/alerts/_tests/test_timeline.py`

**Step 1: Write the failing tests** — an alert carrying `severity_reevaluated` renders
`CRITICAL to WARNING`, the value and the thresholds as a sentence; an alert carrying
`reevaluated_on_config_change` renders the same shape; an alert carrying neither renders
"Never re-evaluated."; the incident timeline entry for a `reevaluated` event carries the
severity change in its `detail`.

**Step 2: Run** → FAIL

**Step 3: Implement**

- A `reevaluation_display` readonly field on `AlertAdmin`, in a fieldset of its own
  above Metadata. It parses both annotation keys (they are JSON strings) and renders
  sentences. A malformed value renders "Could not read the re-evaluation record."
  rather than raising: this is a display, and the alert page must always open.
- In `build_incident_timeline`, when `h.details` carries `severity_from` /
  `severity_to`, append them to `detail`.

**Step 4: Run** → PASS

**Step 5: Commit**

```bash
git commit -am "feat(admin): show whether a re-evaluation touched this alert"
```

---

### Task 12: The policy page's spine widens

**Files:**
- Modify: `apps/alerts/policy_overview.py`
- Test: `apps/alerts/_tests/test_policy_overview.py`

**Step 1: Write the failing tests**

- A node with a firing `disk` alert and no `disk` config produces a row.
- That row's status reads as no policy, not as one of the three config statuses.
- A firing alert for a checker with no scorer produces a muted, un-actionable row.
- A node with neither config nor firing alerts still only counts toward `quiet_count`.
- A checker with both config and firing alerts produces exactly one row.

**Step 2: Run** → FAIL

**Step 3: Implement**

Add a fourth status, `NO_POLICY_SET`, and a muted `NOT_REEVALUATABLE`. Extend
`rows_for_node` to take the set of checkers with firing alerts on that node, and union
it with the config checkers when choosing which rows to build. Rank the new statuses in
`_WORST_FIRST`: a firing alert with no policy is a real gap, so it sorts with the
problems; `NOT_REEVALUATABLE` sorts last and does not make a node a problem node,
because nothing can be done about it.

`build_policy_overview` gathers the firing alerts once, grouped by `instance_id` and
checker in memory. It must not query per node.

**Step 4: Run** → PASS

**Step 5: Commit**

```bash
git commit -am "feat(alerts): show checkers that are alerting with no policy"
```

---

### Task 13: Effect, Applied, and a Re-evaluate button

**Files:**
- Modify: `apps/alerts/policy_overview.py`
- Modify: `templates/admin/policy_overview.html`
- Modify: `apps/alerts/admin.py` (a checker-scoped entry point on `NodeAdmin`)
- Test: `apps/alerts/_tests/test_policy_overview.py`

**Step 1: Write the failing tests**

- A row reports `firing` and `would_change` counts, scored once from the same alerts.
- `last_applied` reads the newest `AlertHistory` row for that node and checker whose
  `details["by"]` marks a re-evaluation.
- A row with no history but a firing alert carrying `severity_reevaluated` still reports
  as applied, sourced from ingest.
- A row with neither reports never applied.
- `reeval_url` points at the checker-scoped action, and a row with no scorer has none.
- The whole page builds in a bounded number of queries — assert with
  `assertNumQueries`, so a later edit cannot quietly make it one query per row.

**Step 2: Run** → FAIL

**Step 3: Implement**

- `PolicyRow` gains `firing_count`, `would_change_count`, `last_applied`, `reeval_url`.
- Scoring reuses `_outcome_for` from `reeval_existing`, so the page and the preview
  cannot disagree about what would change.
- `NodeAdmin` gains a checker-scoped variant of the existing action. Prefer a query
  parameter on the existing `reevaluate_open_alerts` tool URL (`?checker=cpu`) over a
  second action, so there is one confirm flow.
- The template gains the three columns and the button. Autoescaping stays on: checker
  names and `instance_id` arrive over a webhook.

**Step 4: Run** `uv run pytest apps/alerts/ -v` → PASS

**Step 5: Commit**

```bash
git commit -am "feat(admin): show what each policy is doing, and act from the row"
```

---

## Phase 4 — finishing

### Task 14: Docs

**Files:**
- Modify: `apps/alerts/AGENTS.md`
- Modify: `docs/plans/2026-09-06-reevaluation-surface-design.md` (status line only)

Record the new scorer contract (`Verdict | Skip`, every `Skip` a passthrough), the
`ReevalScope`, and that an applied re-evaluation announces. Mark the design as shipped.

Do **not** edit `docs/plans/2026-09-03-policy-overview-design.md` beyond a dated
superseded remark: plan documents are historical records.

```bash
git commit -am "docs: record the re-evaluation surface"
```

---

### Task 15: Verification

**REQUIRED SUB-SKILL:** Use superpowers:verification-before-completion.

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py check
```

Every one must be green, and coverage on the changed lines must be 100% branch. Read
the coverage report rather than assuming; the `Skip` branches are exactly the kind that
slip through.

Then open the PR against `main`.

{% endraw %}
