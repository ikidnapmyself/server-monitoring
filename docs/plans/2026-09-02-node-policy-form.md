---
title: "Node policy form — implementation"
parent: Plans
---

{% raw %}

# Node Policy Form Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the raw JSON textarea on `Node.config` with a validating per-checker policy form, so a typo'd threshold is rejected at the keyboard instead of silently doing nothing forever.

**Architecture:** A new `apps/alerts/node_policy.py` derives the editable field spec from `reevaluation.SCORERS` and `PRIMARY_METRIC`, converts a `Node.config` dict to and from flat form values while preserving keys it does not understand, and validates using the same predicates the scorers use. A `ModelForm` on `NodeAdmin` builds its fields dynamically from that spec for the checkers a given node cares about. Storage is unchanged: no model change, no migration.

**Tech Stack:** Django 5.2 forms + admin, pytest + pytest-django, `uv`.

**Design doc:** `docs/plans/2026-09-02-node-policy-form-design.md`. Read it first.

**Before you start:**

```bash
uv sync --extra dev
uv run pytest apps/alerts -q     # must be green before you change anything
```

---

## Background you must read before Task 1

Open `apps/alerts/reevaluation.py` and read it end to end. It is short. The parts that matter:

**`PRIMARY_METRIC`** (line 26) — seven checkers and the one metric key each is judged on.

**`SCORERS`** (line 160) — the dispatch table. Eight entries: the seven numeric checkers mapped to `_score_numeric`, plus `listening_ports` mapped to `_score_allowlist`. **This is the authority on which checkers accept policy.** Do not hand-write that list anywhere.

**The fail-open contract.** Every scorer returns `None` for any input it cannot use, meaning "passthrough, keep the node's own verdict". This module runs inside the ingest path and must never raise on a node's push. Read `_score_numeric` (line 48) and note the specific things it silently tolerates:

- `cfg` not a dict
- either threshold missing or non-numeric (`_number` rejects `bool`, which is an `int` subclass)
- `critical_threshold < warning_threshold` — an inverted policy is treated as malformed

and `_score_allowlist` (line 111):

- `allowlist` not a list
- any element not coercible to int by `_int_set` (again, `bool` rejected)

**`reevaluation.py` is not being modified by this plan.** Its leniency is correct. The whole point of this work is that the *form* has no reason to be equally lenient: a human typing a threshold can be told immediately that they are wrong. Strict at the keyboard, fail-open at ingest.

**Also read:** `apps/alerts/reeval_existing.py` for `preview_node_alert_reeval` / `apply_node_alert_reeval` and the `ReevalReport` shape, and `apps/alerts/node_overview.py` for `build_checker_rows`, which already computes which checkers a node reports.

---

### Task 1: the field spec, derived from SCORERS

**Files:**
- Create: `apps/alerts/node_policy.py`
- Test: `apps/alerts/_tests/test_node_policy.py`

**Step 1: Write the failing test**

```python
from django.test import TestCase

from apps.alerts.node_policy import FIELD_SPECS, PolicyField, spec_for
from apps.alerts.reevaluation import PRIMARY_METRIC, SCORERS


class FieldSpecTests(TestCase):
    def test_every_configurable_checker_has_a_spec(self):
        # SCORERS is the authority on what accepts policy. If it grows, the form
        # must grow with it without anyone editing the form.
        self.assertEqual(set(FIELD_SPECS), set(SCORERS))

    def test_numeric_checkers_take_two_thresholds(self):
        fields = spec_for("cpu")
        self.assertEqual([f.name for f in fields], ["warning_threshold", "critical_threshold"])
        self.assertTrue(all(f.kind == "number" for f in fields))

    def test_listening_ports_takes_an_allowlist(self):
        fields = spec_for("listening_ports")
        self.assertEqual([f.name for f in fields], ["allowlist"])
        self.assertEqual(fields[0].kind, "int_list")

    def test_every_numeric_checker_in_primary_metric_is_covered(self):
        for checker in PRIMARY_METRIC:
            self.assertEqual([f.name for f in spec_for(checker)],
                             ["warning_threshold", "critical_threshold"])

    def test_a_checker_with_no_policy_has_no_spec(self):
        self.assertEqual(spec_for("raid"), [])
```

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/alerts/_tests/test_node_policy.py -v
```

Expected: `ModuleNotFoundError: apps.alerts.node_policy`.

**Step 3: Implement**

```python
"""The editable shape of ``Node.config``, derived from the scorers that read it.

``apps.alerts.reevaluation`` is deliberately fail-open: it runs in the ingest
path and returns ``None`` (passthrough) for any policy it cannot use, so a
malformed threshold is silently indistinguishable from no policy at all. This
module is the other half of that bargain — the editor, where a human is typing
and can simply be told they are wrong.

The spec is derived from ``SCORERS`` rather than restated, so adding a scorer
adds a form section with no edit here.
"""

from dataclasses import dataclass

from apps.alerts.reevaluation import PRIMARY_METRIC, SCORERS


@dataclass(frozen=True)
class PolicyField:
    name: str
    kind: str  # "number" | "int_list"
    label: str
    help_text: str


_NUMERIC_FIELDS = [
    PolicyField(
        name="warning_threshold",
        kind="number",
        label="Warning at",
        help_text="Raise a warning at or above this value.",
    ),
    PolicyField(
        name="critical_threshold",
        kind="number",
        label="Critical at",
        help_text="Raise a critical at or above this value. Must not be below the warning.",
    ),
]

_ALLOWLIST_FIELDS = [
    PolicyField(
        name="allowlist",
        kind="int_list",
        label="Allowed ports",
        help_text="Comma-separated port numbers. Any listening port not listed is flagged.",
    ),
]

FIELD_SPECS: dict[str, list[PolicyField]] = {
    **{checker: _NUMERIC_FIELDS for checker in PRIMARY_METRIC},
    "listening_ports": _ALLOWLIST_FIELDS,
}

# Guard the derivation rather than trusting it: if SCORERS gains a checker with
# no spec here, the form would silently omit it, which is the failure this whole
# module exists to prevent.
assert set(FIELD_SPECS) == set(SCORERS), (
    "every checker in reevaluation.SCORERS needs a FIELD_SPECS entry"
)


def spec_for(checker: str) -> list[PolicyField]:
    """The editable fields for one checker; empty for a checker with no policy."""
    return FIELD_SPECS.get(checker, [])
```

Note the module-level `assert` will make an unspecced scorer fail at import, loudly, rather than silently vanishing from the form. Confirm that is acceptable in this codebase — if a bare `assert` at import is unusual here, raise a clear exception instead and say so in your report.

**Step 4: Verify**

```bash
uv run pytest apps/alerts/_tests/test_node_policy.py -v
```

**Step 5: Commit**

```bash
git add apps/alerts/node_policy.py apps/alerts/_tests/test_node_policy.py
git commit -m "feat(alerts): derive the node policy field spec from the scorers"
```

---

### Task 2: validation that mirrors the scorers

**Files:** `apps/alerts/node_policy.py`, `apps/alerts/_tests/test_node_policy.py`

Every rule `_score_numeric` and `_score_allowlist` silently tolerate becomes an error here.

**Step 1: Write the failing test**

```python
class ValidationTests(TestCase):
    def test_a_threshold_must_be_a_number(self):
        with self.assertRaises(PolicyError):
            clean_number("not-a-number")

    def test_a_bool_is_not_a_number(self):
        # bool is an int subclass; reevaluation._number rejects it and so must we.
        with self.assertRaises(PolicyError):
            clean_number(True)

    def test_critical_below_warning_is_rejected(self):
        # _score_numeric treats an inverted pair as malformed and passes through,
        # so today this saves cleanly and then does nothing. That is the bug.
        with self.assertRaises(PolicyError):
            clean_thresholds(warning=90.0, critical=80.0)

    def test_equal_thresholds_are_allowed(self):
        # _score_numeric only rejects crit < warn, so equal is a valid policy.
        clean_thresholds(warning=90.0, critical=90.0)

    def test_an_allowlist_parses_comma_separated_ports(self):
        self.assertEqual(clean_int_list("22, 443,8080"), [22, 443, 8080])

    def test_an_empty_allowlist_is_an_empty_list_not_none(self):
        # An empty allowlist is meaningful to _score_allowlist: it means
        # "flag only externally-exposed ports". It is not the same as no policy.
        self.assertEqual(clean_int_list(""), [])

    def test_a_non_integer_port_is_rejected(self):
        with self.assertRaises(PolicyError):
            clean_int_list("22, http")

    def test_a_port_out_of_range_is_rejected(self):
        with self.assertRaises(PolicyError):
            clean_int_list("70000")
```

Check the empty-allowlist claim against `_score_allowlist` before relying on it — read the `if allowset or entry.get("exposed")` line at `reevaluation.py:106` and confirm that an empty allowlist really does mean "exposed ports only" rather than "no policy". If it does not, fix the test and tell me.

The port-range rule is new — the scorers do not check it. That is fine and is the point: the form may be *stricter* than the runtime, never looser. But say in your report that you added a rule the scorers do not have.

**Step 2-4:** run, implement `PolicyError`, `clean_number`, `clean_thresholds`, `clean_int_list` in `node_policy.py`, run again.

`clean_number` must reuse the same rejection rule as `reevaluation._number` rather than restating it. Import and use it if that is clean; if importing a private helper across modules is wrong here, say so and duplicate it with a comment pointing at the original, in the style of `reevaluation.py:19-22`.

**Step 5: Commit**

```bash
git commit -m "feat(alerts): validate node policy at the keyboard, not just at ingest"
```

---

### Task 3: config to form values and back, preserving the unknown

**Files:** `apps/alerts/node_policy.py`, `apps/alerts/_tests/test_node_policy.py`

**Step 1: Write the failing test**

```python
class RoundTripTests(TestCase):
    def test_a_config_becomes_flat_form_values(self):
        config = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        self.assertEqual(
            to_form_values(config),
            {"policy__cpu__warning_threshold": 80, "policy__cpu__critical_threshold": 95},
        )

    def test_an_allowlist_renders_comma_separated(self):
        config = {"listening_ports": {"allowlist": [22, 443]}}
        self.assertEqual(to_form_values(config)["policy__listening_ports__allowlist"], "22, 443")

    def test_form_values_become_a_config(self):
        values = {"policy__cpu__warning_threshold": 80.0, "policy__cpu__critical_threshold": 95.0}
        self.assertEqual(
            to_config(values, existing={}),
            {"cpu": {"warning_threshold": 80.0, "critical_threshold": 95.0}},
        )

    def test_unknown_keys_survive_a_save(self):
        # Nothing an operator authored is ever silently deleted.
        existing = {"cpu": {"warning_threshold": 80}, "made_up": {"anything": 1}}
        result = to_config({"policy__cpu__warning_threshold": 99.0}, existing=existing)
        self.assertEqual(result["made_up"], {"anything": 1})

    def test_an_unknown_key_inside_a_known_checker_survives(self):
        existing = {"cpu": {"warning_threshold": 80, "future_option": "x"}}
        result = to_config({"policy__cpu__warning_threshold": 99.0}, existing=existing)
        self.assertEqual(result["cpu"]["future_option"], "x")
        self.assertEqual(result["cpu"]["warning_threshold"], 99.0)

    def test_a_config_with_no_edits_round_trips_unchanged(self):
        config = {"cpu": {"warning_threshold": 80, "critical_threshold": 95},
                  "listening_ports": {"allowlist": [22]},
                  "made_up": {"anything": 1}}
        self.assertEqual(to_config(to_form_values(config), existing=config), config)

    def test_clearing_a_field_removes_it(self):
        existing = {"cpu": {"warning_threshold": 80, "critical_threshold": 95}}
        result = to_config({"policy__cpu__warning_threshold": None,
                            "policy__cpu__critical_threshold": None}, existing=existing)
        self.assertNotIn("warning_threshold", result.get("cpu", {}))

    def test_a_non_dict_config_does_not_crash(self):
        # Node.config is a JSONField; nothing stops a string being written to it.
        self.assertEqual(to_form_values("not-a-dict"), {})
```

`test_a_config_with_no_edits_round_trips_unchanged` is the load-bearing one: opening a node and saving without touching anything must not alter its policy. Note it compares `80` (int) against whatever comes back — if the round trip turns ints into floats, that test fails and you must decide whether that is acceptable. **It is not** — a config that changes type on an untouched save will make the Task 9 "did anything change" check fire spuriously. Preserve the original value when the form value is numerically equal.

**Step 2-4:** run, implement, run.

**Step 5: Commit**

```bash
git commit -m "feat(alerts): convert node policy between config and form values"
```

---

### Task 4: which checkers a node's form shows

**Files:** `apps/alerts/node_policy.py`, `apps/alerts/_tests/test_node_policy.py`

The union of the checkers this node reports and any its config already names, intersected with what actually accepts policy.

**Step 1: Write the failing test**

```python
class SectionSelectionTests(TestCase):
    def test_a_node_shows_sections_for_the_checkers_it_reports(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._peer_alert(node, "cpu")
        self.assertEqual(sections_for(node), ["cpu"])

    def test_a_configured_checker_shows_even_if_no_longer_reported(self):
        # Policy must never become invisible just because a checker went quiet.
        node = Node.objects.create(instance_id="web-03", hostname="web-03",
                                   config={"disk": {"warning_threshold": 90}})
        self.assertEqual(sections_for(node), ["disk"])

    def test_reported_and_configured_are_unioned_without_duplicates(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03",
                                   config={"cpu": {"warning_threshold": 90}})
        self._peer_alert(node, "cpu")
        self._peer_alert(node, "memory")
        self.assertEqual(sections_for(node), ["cpu", "memory"])

    def test_a_reported_checker_that_accepts_no_policy_is_omitted(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self._peer_alert(node, "raid")   # not in SCORERS
        self.assertEqual(sections_for(node), [])

    def test_a_node_with_nothing_reported_or_configured_shows_no_sections(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertEqual(sections_for(node), [])

    def test_sections_are_sorted(self):
        ...
```

Write `_peer_alert` to create an `Alert` with `node=node` and `labels={"checker": name}` — copy the helper shape from `CheckerStateTests` in `apps/alerts/_tests/test_node_overview.py`.

**Step 3: Implement**

Reuse `apps.alerts.node_overview.build_checker_rows` to learn what the node reports rather than writing a second query — it already handles the local-vs-peer split. Check for an import cycle (`node_overview` will import `node_policy` in Task 5); if one appears, **stop and tell me** rather than working around it. Moving the reported-checkers query into `node_policy` and having `node_overview` keep its own is an acceptable answer, but I want to choose it.

**Step 5: Commit**

```bash
git commit -m "feat(alerts): a node's policy form covers what it reports or configures"
```

---

### Task 5: the form

**Files:** Create `apps/alerts/forms.py` (check first whether one exists), test in `apps/alerts/_tests/test_node_policy_form.py`

A `ModelForm` on `Node` that builds its policy fields dynamically in `__init__` from `sections_for(self.instance)`, and assembles `config` in `clean`.

**Step 1: Write the failing test**

```python
class NodePolicyFormTests(TestCase):
    def test_fields_are_built_for_the_nodes_sections(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03",
                                   config={"cpu": {"warning_threshold": 80,
                                                   "critical_threshold": 95}})
        form = NodePolicyForm(instance=node)
        self.assertIn("policy__cpu__warning_threshold", form.fields)
        self.assertEqual(form.initial["policy__cpu__warning_threshold"], 80)

    def test_the_raw_config_field_is_not_editable(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03")
        self.assertNotIn("config", NodePolicyForm(instance=node).fields)

    def test_an_inverted_pair_is_a_field_error_not_a_silent_save(self):
        node = Node.objects.create(instance_id="web-03", hostname="web-03",
                                   config={"cpu": {"warning_threshold": 1}})
        form = NodePolicyForm(instance=node, data={
            "policy__cpu__warning_threshold": "90",
            "policy__cpu__critical_threshold": "80",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("critical_threshold", str(form.errors))

    def test_a_valid_save_writes_the_config(self):
        ...

    def test_saving_an_untouched_form_leaves_config_byte_identical(self):
        ...
```

That last test is the one that protects every operator who opens a node to look at it and hits Save out of habit.

**Step 3: Implement**

Field naming is `policy__{checker}__{field}`. `number` kind maps to a `FloatField(required=False)`, `int_list` to a `CharField(required=False)` cleaned through `clean_int_list`. Wire the Task 2 validators through the form's `clean_<field>` / `clean` so errors land on the right field. `config` goes in `exclude`; the form assembles it from `to_config(...)` against `self.instance.config` so unknown keys survive.

**Step 5: Commit**

```bash
git commit -m "feat(alerts): a validating per-checker policy form for Node"
```

---

### Task 6: wire it into NodeAdmin

**Files:** `apps/alerts/admin.py`, `apps/alerts/_tests/test_node_admin.py`

Set `form = NodePolicyForm` on `NodeAdmin`. Remove `config` from `fields` (the form now owns it) and drop the `JSONEditorWidget` override **only if no other admin in that file relies on it** — check with a grep, since `formfield_overrides` at `admin.py:686` applies to every `JSONField` on that admin.

Delete the now-stale comment at `admin.py:687-689` describing the config shape by hand; the form is the documentation now.

TDD with a page-level test: load the change page, assert the threshold inputs are present and the raw JSON textarea is gone.

**Commit:** `feat(admin): the node page edits policy as a form, not raw JSON`

---

### Task 7: add policy for a checker the node has not reported

**Files:** `apps/alerts/forms.py`, `apps/alerts/admin.py`, tests

A `ChoiceField` listing `SCORERS` keys not already in `sections_for(node)`. On save with a selection, write an empty policy dict for that checker.

An empty dict is deliberately safe: `_reevaluate` at `reevaluation.py:189` short-circuits on `if not isinstance(cfg, dict) or not cfg`, so `{"cpu": {}}` changes no behavior — it only makes the section appear on the next load. **Verify that read of the code before relying on it.**

Test that adding a checker makes its section appear, and that an empty policy does not alter scoring.

**Commit:** `feat(admin): add policy for a checker a node has not reported yet`

---

### Task 8: show the unknown keys

**Files:** `apps/alerts/node_policy.py`, `templates/admin/alerts/node/change_form.html`, tests

A function returning the config entries no scorer reads — both whole unknown checkers and unknown keys inside known checkers. Render them read-only in the template with a note that nothing honors them.

They are already preserved (Task 3); this only makes them visible. A stale key left by a removed checker should look stale, not invisible.

**Commit:** `feat(admin): surface node config keys no scorer reads`

---

### Task 9: save shows what it would change

**Files:** `apps/alerts/admin.py`, `apps/alerts/_tests/test_node_admin.py`

Override `response_change` on `NodeAdmin`. If the save changed anything scoring-relevant, redirect to the existing re-evaluate preview instead of back to the changelist.

Reuse the existing action rather than building a second path. Find the URL that `django_object_actions` registers for `reevaluate_open_alerts` — **verify the URL name by reading the library or reversing it in a shell**, do not guess it.

"Scoring-relevant" means the parts of `config` that `SCORERS` reads. A save that only changed an unknown key must not redirect. Compare the pre-save and post-save config in `save_model` and stash the verdict for `response_change`.

Tests: a threshold change redirects; an untouched save does not; a change to an unknown-only key does not.

**Commit:** `feat(admin): saving node policy shows what it would change`

---

### Task 10: docs and the full gate

**Files:** `apps/alerts/AGENTS.md`

Note that `Node.config` is edited through `apps/alerts/node_policy.py` + the form, that the spec derives from `reevaluation.SCORERS` so a new scorer gets a form section for free, and the strict-at-the-keyboard / fail-open-at-ingest split with one line on why.

Then:

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run bandit -r apps/ config/ -c pyproject.toml
uv run pip-audit --strict --desc
uv run python manage.py check
```

100% branch coverage on every changed file. Write the test, never a pragma.

**Verify before claiming done:** REQUIRED SUB-SKILL: superpowers:verification-before-completion. Load a node in a browser, set an inverted threshold, and confirm you get a field error rather than a clean save. Then set a valid one and confirm the preview appears.

**Commit:** `docs: the node policy form and the strict-editor rule`

{% endraw %}
