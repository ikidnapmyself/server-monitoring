---
title: "Sysadmin Console PR 8: Nodes and Policy"
parent: Plans
---

{% raw %}

# Sysadmin console PR 8: nodes and policy

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Nodes, the per-node policy editor, node re-evaluation and the fleet policy table get
console pages. `NodeAdmin` drops to stock and read-only, and `/admin/policy/` goes.

**Architecture:** `apps/alerts` owns every rule and the one new write, `node_policy.save_policy`,
a conditional `UPDATE` that refuses a stale editor. `apps/console` owns the reads
(`queries/nodes.py`, `queries/node.py`, `queries/reeval.py`, `queries/policy.py`) and the pages
(`views/nodes.py`, `views/node.py`, `views/node_policy.py`, `views/node_reeval.py`,
`views/policy.py`). The node list copies the alert list's chips and outerHTML polling. The node
page copies the run page's wrapper polling. The editor is a plain form. Both re-evaluate pages
share one preview builder and one partial. Design:
`docs/plans/2026-10-18-sysadmin-console-pr8-nodes-design.md`.

**Tech stack:** Django 5.2 views, forms and templates, htmx, pytest-django.

**Rules for every task:** absolute imports. No comments that restate code. A line length of 100.
No wording on a page carries a UUID, a `snake_case` token or a `{` outside Details. Checker
names render through `checker_label`. `Alert.started_at` is NOT NULL, so every fixture alert
sets it. No test drains a run: an apply enqueues `PENDING` runs and nothing drains them. Run
`uv run black . && uv run ruff check . --fix` before each commit. Pre-commit runs black, ruff,
the full pytest suite and mypy, so a commit takes several minutes. End each commit message with
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

A dataclass this plan lists as "fields: a, b, c" is `@dataclass(frozen=True)` with exactly those
fields, typed as the code that fills it implies.

## Where this plan corrects the design

1. **There is no sidebar.** The console's nav is the underline nav in
   `templates/console/base.html`. Nodes goes after Inbox there, with the `server` octicon, which
   the sprite already holds.
2. **`ReadOnlyAdmin` lives in `apps/orchestration/admin.py`.** Importing it into
   `apps/alerts/admin.py` would couple two apps' admin modules. The three stock admins already in
   `apps/alerts/admin.py` spell the three `has_*_permission` methods inline, and `NodeAdmin`
   follows them.
3. **The policy link is in `SECTION_LINKS`, not `SECTION_MAP`.** `SECTION_LINKS["Operations"]`
   goes. `alerts.node` stays in `SECTION_MAP`, because the model stays registered and the
   completeness test needs it.
4. **The editor must not save through `form.save()`.** `Node.last_seen` is `auto_now`, so the
   admin's `ModelForm` save bumped it, and a silent peer looked fresh after a policy edit.
   `save_policy` is one `QuerySet.update` on the pk and the stored config. Skipping `auto_now` is
   the point here, so no timestamp is set. On SQLite a `JSONField` exact match compares the
   serialized text. The stored dict is re-serialized from the value just read, so it matches any
   row Django wrote. A row written by raw SQL with other spacing would always answer 409.
5. **The preview rows say "this alert".** `describe_reeval_report` was written for one alert. A
   node-wide preview lists many, so it gains `named=True`, which leads each change with
   `alert_name` and links the alert.
6. **The admin confirm stated consequences the PR 6 partial doesn't.** `apps/alerts/AGENTS.md`
   requires the run count and the sweep warning before anything is written. A new
   `describe_reeval_consequences` feeds the shared partial, so the alert page gains them too.
7. **`node_overview` builds an admin URL for a local checker row**, and the admin template linked
   the preflight run. The console builds no admin URL outside `intents.py`. `CheckerRow` gains an
   `action`, and two new intents point at the admin: `view_check_run` and `view_preflight_run`.
8. **Recent runs and incidents are rebuilt, not reused.** `build_pipeline_rows` prints `run_id`,
   a UUID. `build_incident_rows` lists every status and matches the `node` FK only, while the
   console incident list matches through `on_node`. The node page reuses
   `build_run_list(RunFilter(node=pk))`, and its counts, cards and chip links all read one
   `open_incidents_by_node` that follows the `on_node` rule. Both old builders go in Task 11.
9. **`policy_overview` prints raw names.** Its `why` sentences say "Nothing reads
   listening_ports → foo" and "so disk scores as the node sends it". Checker names go through
   `checker_label`, and stored keys move to `PolicyRow.unread` for Details. Its links become
   `Action`s (`edit_node_policy`, `reevaluate_node`, `view_node`), so a reader sees none.
10. **`PolicySection.title` and the add-section choices use `replace("_", " ")`.** Both switch to
    `checker_label`.
11. **Editor anchors.** The admin anchored on the first input's id. The console editor wraps each
    checker in `<fieldset id="policy-<checker>">`, and `editor_anchor(checker)` is the one
    spelling. `EditorLink`, `editor_link` and `PolicySection.anchor` go.
12. **`editor_note` says "the boxes below".** The console node page has no boxes below. It
    becomes "the editor is stricter than the scorers" in Task 11, once the admin tests that pin
    the old words are gone.
13. **`django-object-actions` has no user after the cut.** The dependency, its `INSTALLED_APPS`
    entry and `config/_tests/test_admin_object_actions.py` go.
14. **`AlertSkip.fix_url` fed only the deleted confirm template.** It goes.
15. **"Problem rows sort first"** is already true per node group in `build_policy_overview`. The
    plan keeps that order.
16. **An empty `?checker=` means the whole node**, as the admin's `if checker:` did. Any
    non-empty value narrows, even when it matches nothing.

---

### Task 1: A policy save that refuses a stale editor

**Files:**
- Modify: `apps/alerts/node_policy.py`, `apps/alerts/forms.py`
- Test: `apps/alerts/_tests/test_node_policy.py`, `apps/alerts/_tests/test_node_policy_form.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_node_policy.py`. The race is the case that matters, so it is
spelled out:

```python
from datetime import timedelta
from unittest.mock import patch

import pytest

from apps.alerts.node_policy import StalePolicy, config_digest, editor_anchor, save_policy


@pytest.mark.django_db
def test_a_save_that_loses_the_race_after_its_read_writes_nothing():
    node = Node.objects.create(instance_id="n1", config={"cpu": {"warning_threshold": 80}})
    old = dict(node.config)
    Node.objects.filter(pk=node.pk).update(config={"cpu": {"warning_threshold": 60}})

    with patch("apps.alerts.node_policy._stored_config", return_value=[old]):
        with pytest.raises(StalePolicy):
            save_policy(node.pk, {}, expected=config_digest(old))

    node.refresh_from_db()
    assert node.config == {"cpu": {"warning_threshold": 60}}
```

And these, `@pytest.mark.django_db` where they touch the database:

- `test_the_digest_ignores_key_order`: `{"a": 1, "b": {"c": 2}}` and `{"b": {"c": 2}, "a": 1}`
  hash alike, and `{"a": 1}` differs from `{"a": 2}`.
- `test_a_save_with_the_current_digest_writes_and_keeps_last_seen`: `last_seen` moved two hours
  back with `.update()`. `save_policy` returns the old config, the new one is stored, and
  `last_seen` is unchanged.
- `test_a_save_with_an_old_digest_writes_nothing`: another write lands first. `StalePolicy` is
  raised, and that write survives.
- `test_a_missing_node_is_stale`: `save_policy(999_999, {}, expected=config_digest({}))` raises.
- `test_the_editor_anchor_names_only_a_checker_with_boxes`: `"policy-cpu"` for `cpu`, `""` for
  `raid`.

In `test_a_section_is_titled_for_a_human` (line 660), the title becomes
`checker_label("listening_ports")`.

Append `test_policy_groups_follow_the_sections_and_the_select_reads_as_labels` to
`apps/alerts/_tests/test_node_policy_form.py`. A node with config `{"listening_ports":
{"allowlist": [22]}}` gives `policy_groups()` of one group, `listening_ports`, whose one field is
named `policy__listening_ports__allowlist`. The add select's label for `disk_inodes` is
`checker_label("disk_inodes")`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_node_policy.py apps/alerts/_tests/test_node_policy_form.py -q`
Expected: FAIL with `ImportError: cannot import name 'StalePolicy'`.

**Step 3: Implement**

In `apps/alerts/node_policy.py`, add `import hashlib`, `import json`,
`from apps.alerts.models import Node` and `from apps.checkers.checkers import checker_label`, and:

```python
class StalePolicy(Exception):
    """The stored policy changed between the editor's render and its save."""


def config_digest(config) -> str:
    """The policy an editor was rendered from, as one value its form carries back."""
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()


def _stored_config(pk: int) -> list:
    return list(Node.objects.filter(pk=pk).values_list("config", flat=True))


def save_policy(pk: int, config: dict, *, expected: str):
    """Replace a node's policy iff it still hashes to ``expected``. Return the one replaced.

    A plain ``UPDATE``, so ``last_seen`` keeps saying when the machine last reported.
    """
    stored = _stored_config(pk)
    if not stored or config_digest(stored[0]) != expected:
        raise StalePolicy
    # SQLite ignores row locks. Matching the policy just read means a save that landed in
    # between wins, and this update matches no row.
    if not Node.objects.filter(pk=pk, config=stored[0]).update(config=config):
        raise StalePolicy
    return stored[0]


def editor_anchor(checker: str) -> str:
    """The id of a checker's box group on the console editor, or blank when it has none."""
    return f"policy-{checker}" if spec_for(checker) else ""
```

In `build_effective_policy`, `title = checker_label(checker)`.

In `apps/alerts/forms.py`, `_build_add_field` builds its choices as `(c, checker_label(c))`, and
`NodePolicyForm` gains:

```python
    def policy_groups(self) -> list[tuple[str, list[forms.BoundField]]]:
        """Each section's checker and its bound boxes, in the order the form built them."""
        return [
            (checker, [self[field_name(checker, field.name)] for field in spec_for(checker)])
            for checker in self._sections
        ]
```

**Step 4: Run the alerts suite**

Run: `uv run pytest apps/alerts/ -q`
Expected: PASS. If another test pinned a lowercase section title, update it to the label.

**Step 5: Commit**

```bash
git add apps/alerts
git commit -m "feat(alerts): a node policy save that refuses a stale editor"
```

---

### Task 2: One re-evaluate preview for every page

**Files:**
- Modify: `apps/alerts/describing.py`, `apps/console/queries/alert.py`,
  `apps/console/views/alert_reeval.py`
- Create: `apps/console/queries/reeval.py`, `templates/console/partials/_messages.html`
- Move: `templates/console/alert_reeval.html` to `templates/console/reeval.html`, and
  `templates/console/partials/_alert_reeval.html` to `templates/console/partials/_reeval.html`
- Test: `apps/alerts/_tests/test_describing_reeval.py`, `apps/console/_tests/queries/test_alert.py`,
  `apps/console/_tests/views/test_alert_reeval.py`

**Step 1: Write the failing tests**

Append to `apps/alerts/_tests/test_describing_reeval.py`, with its `_change` helper. It builds an
unsaved `Alert()`, so `pk` is `None` and the name is "Unnamed alert", which is enough here:

- `test_a_named_report_leads_each_change_with_its_alert_and_links_it`: with `named=True` the
  headline starts `f"{alert_name(change.alert)}: would "`, and the action is
  `alert_link(change.alert.pk)`.
- `test_an_unnamed_report_is_unchanged`: the headline starts "Would " and the action is `None`.
- `test_consequences_name_the_runs_and_the_sweep`: a resolving change gives a line starting
  "Applying queues " and one holding "sweeps this node". A report with no changes gives `()`.

In `apps/console/_tests/queries/test_alert.py`, the preview tests read `preview.back_url` for
`preview.alert_url`, and check `preview.title.startswith("Re-evaluate ")` and
`preview.back_text == "Back to the alert"`. In `test_alert_reeval.py`, add
`test_the_preview_states_what_an_apply_queues`: the superuser's GET holds "Applying queues 1
pipeline run.".

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_describing_reeval.py apps/console/ -q`
Expected: FAIL with `ImportError: cannot import name 'describe_reeval_consequences'`.

**Step 3: Implement**

In `apps/alerts/describing.py`:

```python
def _describe_change(change: "AlertChange", named: bool = False) -> Description:
    # the existing headline, tone and detail lines, unchanged
    if named:
        headline = f"{alert_name(change.alert)}: {headline[0].lower()}{headline[1:]}"
    return Description(
        headline=headline,
        tone=tone,
        detail=detail,
        action=alert_link(change.alert.pk) if named else None,
    )


def describe_reeval_report(
    report: "ReevalReport", *, named: bool = False
) -> tuple[Description, ...]:
    """One description per alert the preview looked at. ``named`` when it looked at many."""
    return (
        *(_describe_change(change, named) for change in report.changes),
        *(_describe_skip(skip, report.node) for skip in report.skips),
    )


def describe_reeval_consequences(report: "ReevalReport") -> tuple[str, ...]:
    """What an apply does beyond its rows, said before anything is written."""
    if not report.changes:
        return ()
    runs = report.run_count
    lines = [
        "Every value above is one the node pushed, not a fresh reading.",
        (
            f"Applying queues {runs} pipeline run{'s' if runs != 1 else ''}. "
            "They notify once the inbox drains, if their lane has a channel."
            if runs
            else "Applying queues no pipeline run: no change above belongs to an incident "
            "anyone is notified about."
        ),
    ]
    if report.resolved_count:
        lines.append(
            "Resolving anything also sweeps this node for incidents whose alerts have all "
            "cleared. Each one it resolves gets its own run."
        )
    return tuple(lines)
```

`apps/console/queries/reeval.py` opens with the docstring "The re-evaluate preview every confirm
page shares. Returns dataclasses, never querysets." `ReevalPreview` has fields: title, rows,
notes, warning, empty, can_apply, digest, back_url, back_text, url.

```python
def reeval_preview(
    scope: ReevalScope,
    *,
    title: str,
    back_url: str,
    back_text: str,
    url: str,
    named: bool = False,
    empty: str = "",
) -> ReevalPreview:
    # Before the preview: if the scope moves in between, the apply refuses rather than
    # writing a change the operator never saw.
    digest = scope_digest(scope)
    report = preview_reeval(scope)
    rows = describe_reeval_report(report, named=named)
    return ReevalPreview(
        title=title,
        rows=rows,
        notes=describe_reeval_consequences(report),
        warning=REOPEN_WARNING if report.reopened_count else "",
        empty="" if rows else empty,
        can_apply=bool(report.changes) and report.node is not None,
        digest=digest,
        back_url=back_url,
        back_text=back_text,
        url=url,
    )
```

In `apps/console/queries/alert.py`, delete `ReevalPreview`, import it from `queries/reeval.py`,
and let ruff drop the imports that go unused:

```python
def build_reeval_preview(pk: int) -> ReevalPreview | None:
    alert = Alert.objects.filter(pk=pk).first()
    if alert is None:
        return None
    return reeval_preview(
        ReevalScope.for_alert(alert),
        title=f"Re-evaluate {describe_alert(alert).headline}",
        back_url=reverse("console:alert", args=[alert.pk]),
        back_text="Back to the alert",
        url=reverse("console:alert-reeval", args=[alert.pk]),
    )
```

Templates. `git mv` both files. `partials/_messages.html` is the messages loop `run.html` carries
today, lifted out unchanged so later pages include it. `reeval.html` sets the title block to
`{{ preview.title }} · Console` and includes `_messages.html`, then `_reeval.html`. In
`_reeval.html`, the crumb reads `{{ preview.back_text }}` and links `preview.back_url`, the h1 is
`{{ preview.title }}`, and Cancel links `preview.back_url`. The row loop gains
`{% empty %}<p class="c-muted">{{ preview.empty }}</p>`, and after it
`{% for note in preview.notes %}<p class="c-description__detail">{{ note }}</p>{% endfor %}`.
`views/alert_reeval.py` points at `console/reeval.html` and `console/partials/_reeval.html`.

**Step 4: Run them**

Run: `uv run pytest apps/alerts/ apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add -A apps/alerts apps/console templates/console
git commit -m "refactor(console): one re-evaluate preview, with its consequences stated"
```

---

### Task 3: Fleet policy rows speak in labels and actions

**Files:**
- Modify: `apps/alerts/policy_overview.py`
- Test: `apps/alerts/_tests/test_policy_overview.py`, `config/_tests/test_policy_overview_view.py`

The admin page still reads `edit_url`, `reeval_url` and `node_url`. They stay until Task 11.

**Step 1: Update and add the tests**

In `test_policy_overview.py`, import `checker_label` and `Action`, and change the pinned `why`
strings:

| Line | New expected `why` |
|---|---|
| 94, 102, 167, 317 | `"Nothing reads 1 stored setting."` |
| 109 | `"Nothing reads 2 stored settings."` |
| 118 | `"Set a critical threshold too, or clear both. Nothing reads 1 stored setting."` |
| 157 | `f"{checker_label('cpu')} is set to something that is not a policy, so nothing reads it."` |
| 274 | `f"Alerting now with no policy set, so {checker_label('disk')} scores as the node sends it."` |
| 293 | `f"Alerting now, but no scorer reads {checker_label('raid')}, so no policy can change it."` |

Next to lines 94, 102 and 109, assert `row.unread` is `("network",)`, `("cpu → spare",)` and
`("cpu → old", "cpu → spare")`. Add two tests:

- `test_a_row_carries_its_actions`: a CPU policy and a firing CPU alert on `node-a`. The group's
  `pk` is the node's. With `params = {"pk": node.pk, "checker": "cpu"}`, `row.edit ==
  Action("Edit the policy", "edit_node_policy", params)` and `row.reeval ==
  Action("Re-evaluate", "reevaluate_node", params)`.
- `test_a_checker_with_no_boxes_opens_the_node`: config `{"network": {"x": 1}}` gives
  `row.edit == Action("Open the node", "view_node", {"pk": node.pk})` and `row.reeval is None`.

In `config/_tests/test_policy_overview_view.py:143`, expect `"no scorer reads RAID arrays"`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_policy_overview.py config/_tests/test_policy_overview_view.py -q`
Expected: FAIL on the old wording, and `AttributeError: 'PolicyRow' object has no attribute 'edit'`.

**Step 3: Implement**

In `apps/alerts/policy_overview.py`, import `checker_label` and `Action`. `PolicyRow` gains
`edit: Action | None = None`, `reeval: Action | None = None` and `unread: tuple[str, ...] = ()`.
`NodeGroup` gains `pk: int` as its first field. Add:

```python
def _edit(node, checker: str) -> Action:
    """This checker's boxes on the editor, or the node page for a checker with none."""
    if spec_for(checker):
        return Action("Edit the policy", "edit_node_policy", {"pk": node.pk, "checker": checker})
    return Action("Open the node", "view_node", {"pk": node.pk})


def _reeval(node, checker: str) -> Action:
    return Action("Re-evaluate", "reevaluate_node", {"pk": node.pk, "checker": checker})
```

Every `PolicyRow(...)` that sets `edit_url` also sets `edit=_edit(node, checker)`, so
`_firing_only_row` takes `node`. `rows_for_node` sets
`unread=tuple(entry.label for entry in unread.get(checker, []))`. `_with_evidence` sets `reeval`
under the same condition as `reeval_url`. `build_policy_overview` passes `pk=node.pk`.
`_firing_only_row`'s two sentences use `checker_label(checker)`. The unread sentences:

```python
def _unread_sentences(entries: list[UnreadKey]) -> list[str]:
    """What the unread entries for one checker mean. The keys themselves go to Details.

    A blank ``key`` covers two problems: no scorer knows the checker, or a known checker
    holds something that is not a mapping. ``spec_for`` separates them.
    """
    count = 0
    sentences = []
    for entry in entries:
        if not entry.key and spec_for(entry.checker):
            sentences.append(
                f"{checker_label(entry.checker)} is set to something that is not a policy, "
                "so nothing reads it."
            )
        else:
            count += 1
    if count:
        sentences.append(f"Nothing reads {count} stored setting{'s' if count != 1 else ''}.")
    return sentences
```

**Step 4: Run them**

Run: `uv run pytest apps/alerts/ config/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts config/_tests
git commit -m "feat(alerts): fleet policy rows carry labels and actions"
```

---

### Task 4: The node list

**Files:**
- Modify: `apps/console/filters.py`, `apps/console/queries/incidents.py` (`chip_group` annotation),
  `apps/console/queries/hosts.py`, `apps/console/queries/overview.py`
- Create: `apps/console/queries/nodes.py`, `apps/console/views/node.py`, `apps/console/views/nodes.py`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`, `apps/console/intents.py`,
  `templates/console/base.html`, `templates/console/not_found.html`
- Create: `templates/console/nodes.html`, `templates/console/partials/_nodes.html`,
  `templates/console/partials/_node_row.html`
- Test: `apps/console/_tests/test_filters.py`, `apps/console/_tests/conftest.py`,
  `apps/console/_tests/queries/test_nodes.py`, `apps/console/_tests/queries/test_overview.py`,
  `apps/console/_tests/views/test_nodes.py`, `apps/console/_tests/test_intents.py`

**Step 1: Write the failing tests**

`test_filters.py`:
- `NodeFilter.parse` of nothing is `NodeFilter()`. `freshness=silent&q=+web+` gives
  `NodeFilter(q="web", freshness="silent")`. `freshness=gone` gives
  `NodeFilter(dropped=("freshness",))`.
- `NodeFilter(q="web", freshness="silent").querystring(freshness="") == "?q=web"`, and
  `NodeFilter(freshness="silent").kept() == (("freshness", "silent"),)`.
- `checker_param(QueryDict("checker=+cpu+")) == "cpu"`, and `""` when absent.

`conftest.py` gains `node_viewer` (`bare_staff` plus `alerts.view_node`) and `node_operator`
(`node_viewer` plus `alerts.change_node`), built with `_perm` like the run fixtures.

`queries/test_nodes.py`, each a `@pytest.mark.django_db` test on `build_node_list(NodeFilter(...))`:
- `test_rows_read_worst_first_then_by_name`: a node with a firing critical alert, one with a
  firing warning, a silent peer (`last_seen` two hours back through `.update()`) and two healthy
  ones named `b` and `A`. The tones run critical, warning, muted, ok, ok, and the healthy ones
  come `A`, `b`.
- `test_the_tone_and_label_follow_the_overview_tile`: each row's `(tone, label)` equals its
  tile's from `overview.node_tiles()`.
- `test_the_hub_is_never_silent`: `Node(instance_id=local_instance_id())` with an old
  `last_seen` has `role == "This hub"` and isn't under `freshness="silent"`.
- `test_freshness_chips_count_and_narrow`.
- `test_q_matches_hostname_or_instance_id`, case-insensitively.
- `test_counts_follow_the_incident_list_rule`: an open critical incident with two alerts on the
  node counts once. An acknowledged one counts and a resolved one doesn't. An unlinked alert
  whose `labels["instance_id"]` names the node counts. Each count's `url` is
  `/console/incidents/?severity=<s>&node=<pk>`, and `build_incident_list` over that querystring
  holds exactly the counted incidents.
- `test_an_unnamed_node_reads_unnamed_node`.
- `test_empty_states`: `"none"` with no nodes, `"no_match"` when `q` excludes them all.
- `test_the_query_count_does_not_grow_with_the_nodes`: `CaptureQueriesContext` over 2 and over
  12 nodes, each with an open incident, gives one count.

In `queries/test_overview.py:256`, the unnamed tile's name becomes `"Unnamed node"`.

`views/test_nodes.py`, following `views/test_alerts.py`:
- `reverse("console:nodes") == "/console/nodes/"`.
- Anonymous goes to the admin login, `bare_staff` gets 403, and `view_node` or `change_node`
  reads the list, parametrized.
- With HTMX the body starts with `<div id="nodes"`.
- Rows link the node page, and their counts link the incident list.
- The nav marks Nodes current, with the `_nav` helper from `test_alert.py`.
- `MARKUP` in a hostname renders as `ESCAPED`.

`test_intents.py`: move `view_nodes` out of `test_config_intents_point_at_the_admin` into a new
parametrized `test_node_intents_point_at_the_console` (intent, params, url, method), starting
with `("view_nodes", {}, "/console/nodes/", "get")`. Add `("view_nodes", {}, "alerts", "node")`
to `test_a_read_link_takes_view_or_change`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/ -q`
Expected: FAIL with `ImportError: cannot import name 'NodeFilter'`.

**Step 3: Implement**

`apps/console/filters.py`:

```python
FRESHNESS_CHOICES: tuple[str, ...] = ("", "silent", "reporting")


@dataclass(frozen=True)
class NodeFilter:
    q: str = ""
    freshness: str = ""
    dropped: tuple[str, ...] = ()

    @classmethod
    def parse(cls, params: QueryDict) -> "NodeFilter":
        values, dropped = _parse(params, (("freshness", FRESHNESS_CHOICES),))
        text = _text(params, "q", SEARCH_LIMIT)
        if text:
            values["q"] = text
        return cls(**values, dropped=tuple(dropped))  # type: ignore[arg-type]

    def querystring(self, **changes: str | int | None) -> str:
        target = replace(self, **changes)
        pairs = [(name, value) for name in ("q", "freshness") if (value := getattr(target, name))]
        return f"?{urlencode(pairs)}" if pairs else ""

    def kept(self) -> tuple[tuple[str, str], ...]:
        return (("freshness", self.freshness),) if self.freshness else ()


def checker_param(params: QueryDict) -> str:
    """The checker a re-evaluate link narrows to, or blank for the whole node."""
    return _text(params, "checker", SEARCH_LIMIT)
```

Add `NodeFilter` to `chip_group`'s `criteria` annotation in `queries/incidents.py`.

`apps/console/queries/hosts.py`:

```python
UNNAMED_NODE = "Unnamed node"


def node_name(hostname: str) -> str:
    return hostname or UNNAMED_NODE
```

`apps/console/queries/overview.py`: rename `_node_badge` to `node_badge` and `_firing_by_node`
to `firing_by_node`. Lift the silence rule out of `node_tiles`, call it there, and name a tile
`node_name(node.hostname)`:

```python
def is_silent(node: Node, cutoff: datetime, self_id: str) -> bool:
    # This instance's own row is never silent: a hub checked by hand is not a quiet agent.
    return node.last_seen < cutoff and node.instance_id != self_id
```

`apps/console/queries/nodes.py`, docstring "Read model for the node list. Returns dataclasses,
never querysets." `SeverityCount` has fields: severity, count, tone, url. `NodeListRow` has
fields: pk, name, role, tone, label, counts, last_seen, url. `NodeList` has fields: rows, groups,
q, kept, policy, notice, empty, url.

```python
SEVERITIES = (AlertSeverity.CRITICAL, AlertSeverity.WARNING, AlertSeverity.INFO)
TONE_RANK = {"critical": 0, "warning": 1, "info": 2, "muted": 3, "ok": 4}
FRESHNESS_CHIPS: tuple[Choice, ...] = (
    ("all", "All", ""),
    ("silent", "Silent", "silent"),
    ("reporting", "Reporting", "reporting"),
)


def open_incidents_by_node(nodes: Iterable[Node]) -> dict[int, dict[str, set[int]]]:
    """Unresolved incident pks per node and severity, by the incident list's node rule.

    A linked alert counts for its node. An unlinked one counts for every node its instance
    labels name, as ``on_node`` matches.
    """
    named: defaultdict[str, set[int]] = defaultdict(set)
    for node in nodes:
        for key in (node.instance_id, node.hostname):
            if key:
                named[key].add(node.pk)
    found: defaultdict[int, defaultdict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    rows = (
        Alert.objects.filter(incident__status__in=OPEN_STATUSES)
        .values_list("incident_id", "incident__severity", "node_id", "labels")
        .order_by()
    )
    for incident_pk, severity, node_pk, labels in rows:
        owners = {node_pk} if node_pk is not None else set()
        if node_pk is None and isinstance(labels, dict):
            for name in ("instance_id", "instance", "hostname"):
                value = labels.get(name)
                if isinstance(value, str):
                    owners |= named.get(value, set())
        for owner in owners:
            found[owner][severity].add(incident_pk)
    return {pk: dict(by_severity) for pk, by_severity in found.items()}


def severity_counts(node_pk: int, by_severity: dict[str, set[int]]) -> tuple[SeverityCount, ...]:
    base = reverse("console:incidents")
    return tuple(
        SeverityCount(
            severity=str(severity),
            count=len(by_severity[severity]),
            tone=tone_for(SEVERITY_TONES, severity),
            url=base + IncidentFilter(severity=str(severity), node=node_pk).querystring(),
        )
        for severity in SEVERITIES
        if by_severity.get(severity)
    )


def build_node_list(criteria: NodeFilter) -> NodeList:
    base = reverse("console:nodes")
    cutoff = timezone.now() - timedelta(minutes=NODE_RECENT_MINUTES)
    self_id = local_instance_id()
    nodes = Node.objects.order_by()
    if criteria.q:
        nodes = nodes.filter(
            Q(hostname__icontains=criteria.q) | Q(instance_id__icontains=criteria.q)
        )
    found = list(nodes)
    silent = {node.pk for node in found if is_silent(node, cutoff, self_id)}
    counts = {"all": len(found), "silent": len(silent), "reporting": len(found) - len(silent)}
    if criteria.freshness:
        want = criteria.freshness == "silent"
        found = [node for node in found if (node.pk in silent) == want]
    firing, critical, warning = firing_by_node()
    open_by_node = open_incidents_by_node(found)
    rows = []
    for node in found:
        tone, label = node_badge(
            critical[node.pk], warning[node.pk], firing[node.pk], node.pk in silent
        )
        rows.append(
            NodeListRow(
                pk=node.pk,
                name=node_name(node.hostname),
                role="This hub" if node.instance_id == self_id else "Peer",
                tone=tone,
                label=label,
                counts=severity_counts(node.pk, open_by_node.get(node.pk, {})),
                last_seen=node.last_seen,
                url=reverse("console:node", args=[node.pk]),
            )
        )
    rows.sort(key=lambda row: (TONE_RANK.get(row.tone, len(TONE_RANK)), row.name.casefold()))
    empty = "" if rows else "no_match" if Node.objects.exists() else "none"
    return NodeList(
        rows=tuple(rows),
        groups=(chip_group("freshness", "Freshness", FRESHNESS_CHIPS, criteria, base, counts),),
        q=criteria.q,
        kept=criteria.kept(),
        policy=Action("Fleet policy", intent="view_policy"),
        notice=(
            f"Ignored invalid filter: {', '.join(criteria.dropped)}." if criteria.dropped else ""
        ),
        empty=empty,
        url=base + criteria.querystring(),
    )
```

The list reverses `console:node`, so this task adds the node page's route with a stub that Task
5 fills. `apps/console/views/node.py`:

```python
from django.shortcuts import render

from apps.console.views.base import ConsoleView

NODE_READ = ("alerts.view_node", "alerts.change_node")
NODE_CHANGE = "alerts.change_node"
CHANGE_REQUIRED = "Permission to change nodes required."


class NodePageView(ConsoleView):
    read_permissions = NODE_READ

    @property
    def can_change(self) -> bool:
        return self.request.user.has_perm(NODE_CHANGE)

    def not_found(self):
        return render(self.request, "console/not_found.html", {"nav": "nodes"}, status=404)


class NodeDetailView(NodePageView):
    def get(self, request, *args, **kwargs):
        return self.not_found()
```

`views/nodes.py` holds `NodeListView`, a copy of `AlertListView` over `NodeFilter`,
`build_node_list`, the `_nodes.html` templates, `nav="nodes"` and `read_permissions = NODE_READ`.
Export both views from `views/__init__.py`. Routes, after the inbox:

```python
    path("nodes/", NodeListView.as_view(), name="nodes"),
    path("nodes/<int:pk>/", NodeDetailView.as_view(), name="node"),
```

Intents: a module-level `NODE_READ = ("alerts.view_node", "alerts.change_node")`, and
`"view_nodes": Intent(_page("console:nodes"), "get", NODE_READ)`.

Templates:
- `nodes.html` copies `alerts.html` with the h1 "Nodes".
- `partials/_nodes.html` copies `_alerts.html`. The root is `<div id="nodes" data-poll
  hx-get="{{ list.url }}" hx-trigger="every 30s, console:refresh" hx-swap="outerHTML">`. The
  search input is `nodes-q`, placeholder "Hostname or instance ID". The freshness group renders
  as the `c-states` tabs with counts, each chip with `hx-sync="#nodes:replace"`. The header holds
  `{% resolved list.policy as act %}{% if act.url %}{% include "console/partials/_action.html"
  with act=act id="nodes-policy" %}{% endif %}`. Blank slates: "No nodes have reported yet."
  ("A node shows up here once it reports to this hub.") and "No nodes match these filters." with
  a Clear filters link. No pager: the node table bounds the list.
- `partials/_node_row.html`:

```html
<li class="c-box__row c-box__row--hover c-row">
  <span class="c-row__icon">{% include "console/partials/_tone_icon.html" with tone=row.tone %}</span>
  <div class="c-row__main">
    <a id="node-{{ row.pk }}" href="{{ row.url }}">{{ row.name }}</a>
    <span class="c-label c-tone-{{ row.tone }}">{{ row.label }}</span>
    {% for c in row.counts %}<a id="node-{{ row.pk }}-{{ c.severity }}" class="c-label c-tone-{{ c.tone }}" href="{{ c.url }}">{{ c.count }} {{ c.severity }}</a>{% endfor %}
    <div class="c-row__meta">{{ row.role }} · seen {% include "console/partials/_time.html" with when=row.last_seen %}</div>
  </div>
</li>
```

- `base.html`: after Inbox, a `c-unav__item` linking `console:nodes`, current when `nav ==
  "nodes"`, with the `server` octicon and the label Nodes.
- `not_found.html`: a `nav == "nodes"` branch, "This node doesn't exist. It may have been
  deleted.", with `not-found-back` linking `console:nodes`.

**Step 4: Run the console suite**

Run: `uv run pytest apps/console/ -q`
Expected: PASS. Fix any other test that used `_node_badge` or `_firing_by_node`.

**Step 5: Commit**

```bash
git add apps/console templates/console
git commit -m "feat(console): the node list"
```

---

### Task 5: The node page

**Files:**
- Modify: `apps/alerts/node_overview.py` (`CheckerRow.action`, `PreflightPanel.run` type),
  `apps/console/queries/alert.py` (`incident_cards`), `apps/console/views/node.py`,
  `apps/console/intents.py`, `static/console/console.css`
- Create: `apps/console/queries/node.py`, `templates/console/node.html`,
  `templates/console/partials/_node.html`
- Test: `apps/alerts/_tests/test_node_overview.py`, `apps/console/_tests/queries/test_node.py`,
  `apps/console/_tests/views/test_node.py`, `apps/console/_tests/test_intents.py`,
  `apps/console/_tests/views/test_overview.py`

**Step 1: Write the failing tests**

`test_node_overview.py`: the peer row's `action` is `Action("Open the alert",
intent="view_alert", params={"pk": alert.pk})`, and a hub row's is `Action("Open the check run",
"view_check_run", {"pk": run.pk})`.

`queries/test_node.py`, each `@pytest.mark.django_db` on `build_node_detail(pk, can_change=...)`:
- `test_a_missing_node_is_none`.
- `test_the_hub_reads_its_checks_charts_and_preflight`: `Node(instance_id=local_instance_id(),
  hostname="hub")`, three CPU `CheckRun` rows on `hostname="hub"`, and
  `PreflightRun(instance_id=local_instance_id(), passed=5, warnings=1, errors=0,
  overall_status="warning")`. `role == "This hub"`, `freshness_tone == "info"`,
  `checkers[0].label == checker_label("cpu")`, one chart titled "CPU", `preflight == "5 passed,
  1 warning, 0 errors."`, and `preflight_action == Action("Open the preflight run",
  "view_preflight_run", {"pk": run.pk})`.
- `test_a_peer_reads_its_alerts_and_has_no_charts`: `charts == ()`, `charts_note ==
  PEER_HISTORY_NOTE`, `preflight == ""`, `preflight_note == PEER_PREFLIGHT_NOTE`.
- `test_a_silent_peer_reads_quiet_and_amber`: `label == "quiet"`, `freshness_tone == "warning"`.
- `test_an_unnamed_node_keeps_its_id_for_details`: `name == "Unnamed node"`, and
  `Fact("Instance ID", node.instance_id, copy=True) in facts`.
- `test_open_incidents_are_cards_worst_first_and_match_the_counts`: an open warning, an open
  critical, an acknowledged info and a resolved one give three cards, critical first, each `url`
  the console incident page, and counts that add up to three.
- `test_recent_runs_are_the_runs_list_rows_for_this_node`: 12 runs with `node=node` give ten
  rows, newest first, and `runs_url == f"/console/runs/?node={node.pk}"`.
- `test_the_policy_reads_in_effect_not_scoring_and_unread`: config `{"cpu":
  {"warning_threshold": 80, "critical_threshold": 90, "warn_level": 3}, "disk_inodes":
  {"warning_threshold": 70}}`. `in_effect[0]` has `label == checker_label("cpu")` and `values ==
  ("Warning at 80", "Critical at 90")`. `not_scoring[0].note` is the critical-threshold
  sentence. `unread == (Fact(checker_label("cpu"), "cpu → warn_level"),)`.
- `test_only_a_changer_gets_actions`, parametrized over `can_change`: `do` is `(Action("Edit
  policy", "edit_node_policy", {"pk": pk}), Action("Re-evaluate open alerts", "reevaluate_node",
  {"pk": pk}))` or `()`, and each policy line's `edit` is `Action("Edit", "edit_node_policy",
  {"pk": pk, "checker": "cpu"})` or `None`.
- `test_raw_holds_labels_and_config`.

`views/test_node.py`, following `views/test_run.py`:
- `reverse("console:node", args=[7]) == "/console/nodes/7/"`.
- Anonymous, `bare_staff` 403, `node_viewer` reads.
- A missing node is the console 404 page, `@BOTH`.
- `node_viewer` gets no `c-do` box. Task 7 adds the operator's buttons.
- The incident card links the console incident.
- A UUID instance id is in the body and not in `visible(body)` from `test_guard.py`.
- `MARKUP` in a hostname and in a label renders as `ESCAPED`.
- `test_the_rendered_page_has_no_inline_script_or_style`: the hub with CPU history, so the
  sparkline renders, scanned with `FORBIDDEN`. `test_templates.py` can't see helper markup.
- `test_every_focusable_element_has_a_stable_id`, with `focus_audit` from `test_incident.py`.

`test_intents.py`: add `("view_node", {"pk": 7}, "/console/nodes/7/", "get")` to the node test,
and `("view_check_run", {"pk": 7}, "/admin/checkers/checkrun/7/change/")` and
`("view_preflight_run", {"pk": 7}, "/admin/checkers/preflightrun/7/change/")` to
`test_config_intents_point_at_the_admin`. Add `("view_node", {"pk": 7}, "alerts", "node")` to
the view-or-change test. `views/test_overview.py:326` and `:338`: the tile links
`reverse("console:node", args=[node.pk])`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_node_overview.py apps/console/ -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.console.queries.node'`.

**Step 3: Implement**

`apps/alerts/node_overview.py`: `CheckerRow` gains `action: Action`. The local row sets
`Action("Open the check run", "view_check_run", {"pk": run.pk})`, the peer row
`alert_link(alert.pk)` from `apps.alerts.describing`. `url` stays until Task 11.
`PreflightPanel.run` is typed `PreflightRun | None`, so mypy accepts `run.passed`.

`apps/console/queries/alert.py`:

```python
def incident_cards(incidents: list[Incident]) -> tuple[IncidentCard, ...]:
    described = describe_incidents(incidents)
    return tuple(
        IncidentCard(
            pk=incident.pk,
            what=described[incident.pk],
            status=incident.status,
            status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
            url=reverse("console:incident", args=[incident.pk]),
        )
        for incident in incidents
    )
```

`apps/console/queries/node.py`, docstring "Read model for one node's page. Returns dataclasses,
never querysets.":

- `CheckerLine` has fields: key, label, status, value, observed_at (typed `object`, as
  `CheckerRow` types it), action.
- `PolicyLine` has fields: key, label, values, note, edit.
- `NodeDetail` has fields: pk, name, role, freshness, freshness_tone, tone, label, counts,
  checkers, checker_time_label, incidents, charts, charts_note, preflight, preflight_at,
  preflight_action, preflight_note, runs, runs_url, in_effect, not_scoring, unread, do, facts,
  raw, url.

```python
RUN_LIMIT = 10


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' if count != 1 else ''}"


def _policy(node: Node, can_change: bool):
    policy = build_effective_policy(node)

    def line(section, note: str) -> PolicyLine:
        edit = Action("Edit", "edit_node_policy", {"pk": node.pk, "checker": section.checker})
        return PolicyLine(
            key=section.checker,
            label=section.title,
            values=tuple(f"{value.label} {value.value}" for value in section.values),
            note=note,
            edit=edit if can_change else None,
        )

    return (
        tuple(line(section, section.editor_note) for section in policy.sections),
        tuple(line(section, section.inactive_reason) for section in policy.inactive),
        tuple(Fact(checker_label(entry.checker), entry.label) for entry in policy.unread),
    )
```

`build_node_detail(pk, *, can_change=False) -> NodeDetail | None` returns `None` for a missing
node, and otherwise starts:

```python
    identity = build_identity(node)
    firing, critical, warning = firing_by_node()
    tone, label = node_badge(
        critical[node.pk], warning[node.pk], firing[node.pk], identity.freshness_status == "warn"
    )
    by_severity = open_incidents_by_node([node]).get(node.pk, {})
    incidents = list(
        Incident.objects.filter(pk__in=set().union(*by_severity.values()))
        .annotate(rank=SEVERITY_RANK)
        .order_by("rank", "created_at")
    )
    run = build_preflight(node, identity).run
    runs = RunFilter(node=node.pk)
    in_effect, not_scoring, unread = _policy(node, can_change)
```

The rest fills `NodeDetail` from those:
- `name=node_name(node.hostname)`, `role=identity.role_label`,
  `freshness=identity.freshness_label`, `freshness_tone=identity.freshness_tone`, and
  `counts=severity_counts(node.pk, by_severity)`.
- `checkers` maps each `build_checker_rows(node, identity)` row to a `CheckerLine` with
  `key=row.checker` and `label=checker_label(row.checker)`. `checker_time_label`, `charts` (as a
  tuple) and `charts_note` come from the `node_overview` functions of those names, each passed
  `identity`.
- `incidents=incident_cards(incidents)`.
- `preflight` is `f"{run.passed} passed, {_plural(run.warnings, 'warning')},
  {_plural(run.errors, 'error')}."`, or `""` with no run. `preflight_at=run.created_at`.
  `preflight_action=Action("Open the preflight run", "view_preflight_run", {"pk": run.pk})`.
  `preflight_note` is the panel's note.
- `runs=build_run_list(runs).rows[:RUN_LIMIT]` and
  `runs_url=reverse("console:runs") + runs.querystring()`.
- `do` holds `Action("Edit policy", "edit_node_policy", {"pk": node.pk})` and
  `Action("Re-evaluate open alerts", "reevaluate_node", {"pk": node.pk})` when `can_change`,
  else `()`.
- `facts` holds "Instance ID" (with `copy=True`), "Address" and "Last source", each only when
  set. `raw` is `json.dumps({"labels": node.labels, "config": node.config}, indent=2,
  sort_keys=True, default=str)`. `url=reverse("console:node", args=[node.pk])`.

`views/node.py`: `NodeDetailView` sets `template_name = "console/node.html"` and
`partial_template_name = "console/partials/_node.html"`. Its `get` renders
`build_node_detail(kwargs["pk"], can_change=self.can_change)` as `node` with `nav="nodes"`, or
returns `self.not_found()`.

Intents: `"view_node": Intent(_record("console:node"), "get", NODE_READ)`, `"view_check_run":
Intent(_record("admin:checkers_checkrun_change"), "get", "checkers.view_checkrun")` and
`"view_preflight_run": Intent(_record("admin:checkers_preflightrun_change"), "get",
"checkers.view_preflightrun")`.

`console.css` gains a table component next to `c-facts`:

```css
.c-table { width: 100%; border-collapse: collapse; }
.c-table th,
.c-table td { padding: var(--space-2) var(--space-3); border-top: 1px solid var(--borderColor-muted); text-align: left; vertical-align: top; }
.c-table th { color: var(--fgColor-muted); font-weight: 600; }
.c-table__num { text-align: right; font-variant-numeric: tabular-nums; }
```

Templates:
- `node.html` copies `run.html`: `_messages.html` outside the wrapper, then `<div id="node"
  data-poll hx-get="{{ node.url }}" hx-trigger="every 30s, console:refresh"
  hx-swap="innerHTML">`.
- `partials/_node.html` copies `_alert.html`'s layout. Crumb "Back to nodes" (`node-back`). The
  pagehead shows `node.name`, a `c-label c-tone-{{ node.tone }}` with `node.label`, each count
  as a link `node-count-{{ c.severity }}`, then the role and "seen {{ node.freshness }}" in
  `c-tone-{{ node.freshness_tone }}`.
- **Checker state**: a `c-table` with Checker, Status, Value (`c-table__num`) and
  `node.checker_time_label`. The label resolves `line.action` and links as
  `checker-{{ line.key }}` when it has a URL.
- **Open incidents**: the alert page's incident card per card, `node-incident-{{ card.pk }}`,
  else "Nothing open on this node.".
- **History**: `{{ chart.svg }}` and "latest {{ chart.latest }}" per chart, else
  `node.charts_note`, else "No history recorded yet.".
- **Policy**: "Policy in effect" lists each line's label, values and an edit link
  `policy-edit-{{ line.key }}` when it resolves, with a note as "Scoring as stored, but
  {{ line.note }}". "Saved but not scoring" lists the same with the note in `c-tone-warning`.
  "Not honoured" reads "{{ node.unread|length }} stored setting{{ node.unread|length|pluralize }}
  no scorer reads." and puts the keys in `_details.html` with `facts=node.unread
  id="node-unread"`. With none of the three: "No policy set on this node.".
- **Recent runs**: `_run_row.html` per run, then `node-runs` "All runs on this node".
- `_details.html` with `facts=node.facts raw=node.raw id="node-details"`.
- The sidebar holds Role, Freshness and Preflight. Preflight shows `node.preflight` and its time,
  linked as `node-preflight` when it resolves, else `node.preflight_note`. "Do this" renders
  `node.do` as the alert page does, the first primary.

**Step 4: Run them**

Run: `uv run pytest apps/alerts/ apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts apps/console templates/console static/console/console.css
git commit -m "feat(console): the node page"
```

---

### Task 6: The policy editor

**Files:**
- Modify: `apps/console/queries/node.py` (`build_policy_editor`), `apps/console/views/__init__.py`,
  `apps/console/urls.py`, `apps/console/intents.py`, `apps/alerts/describing.py`
  (`_describe_skip`), `static/console/console.css`
- Create: `apps/console/views/node_policy.py`, `apps/console/views/node_reeval.py` (a stub Task 7
  fills), `templates/console/node_policy.html`, `templates/console/partials/_node_policy.html`,
  `templates/console/partials/_field.html`
- Test: `apps/console/_tests/views/test_node_policy.py`, `apps/console/_tests/test_intents.py`,
  `apps/alerts/_tests/test_describing_reeval.py`

**Step 1: Write the failing tests**

`views/test_node_policy.py`. The stale save is spelled out:

```python
import pytest
from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_policy import config_digest

pytestmark = pytest.mark.django_db

CPU = {"cpu": {"warning_threshold": 80, "critical_threshold": 90}}


@pytest.fixture
def node():
    return Node.objects.create(instance_id="web-1", hostname="web-1", config=dict(CPU))


def _url(node):
    return reverse("console:node-policy", args=[node.pk])


def _boxes(warning=80, critical=90, **extra):
    return {
        "policy__cpu__warning_threshold": warning,
        "policy__cpu__critical_threshold": critical,
        **extra,
    }


def test_a_stale_save_is_a_409_with_the_fresh_boxes(client, node_operator, node):
    digest = config_digest(node.config)
    Node.objects.filter(pk=node.pk).update(
        config={"cpu": {"warning_threshold": 60, "critical_threshold": 95}}
    )

    response = client.post(_url(node), {"digest": digest, **_boxes(warning=70)})

    body = response.content.decode()
    assert response.status_code == 409
    assert "This node&#x27;s policy changed since you opened it." in body
    assert 'value="60"' in body
    node.refresh_from_db()
    assert node.config["cpu"]["warning_threshold"] == 60
```

Check how Django renders `60` in a `FloatField` box and match it. Then these, each posting the
stored config's `digest` unless it says otherwise:

- `reverse("console:node-policy", args=[7]) == "/console/nodes/7/policy/"`.
- `node_viewer` gets 403 on GET and on POST, and the config is unchanged.
- The GET holds `id="policy-cpu"`, `name="policy__cpu__warning_threshold"` and the digest.
- `test_a_scoring_change_lands_on_the_preview_and_applies_nothing`: `_boxes(warning=70)` gives a
  303 to `reverse("console:node-reeval", args=[node.pk])`, and the stored warning is 70.
- `test_an_untouched_save_lands_on_the_node_page`: `_boxes()` gives a 303 to the node page, which
  then shows "Policy saved.".
- `test_opening_a_section_lands_back_on_its_boxes`: `_boxes(policy__add_section="memory")` gives
  a 303 to `f"{_url(node)}#policy-memory"`, and `node.config["memory"] == {}`.
- `test_an_invalid_box_writes_nothing_and_keeps_the_posted_digest`: `{"digest": "posted",
  "policy__cpu__warning_threshold": 80}` gives a 200 holding "Set a critical threshold too, or
  clear both." and `value="posted"`, and the config is unchanged.
- A missing node is the console 404 page.

`test_intents.py`, in the node test: `("edit_node_policy", {"pk": 7}, "/console/nodes/7/policy/",
"get")` and `("edit_node_policy", {"pk": 7, "checker": "cpu"},
"/console/nodes/7/policy/#policy-cpu", "get")`. A checker with no boxes, such as `raid`, gets no
fragment.

`test_describing_reeval.py:262`: the fixable skip's action is `Action("Edit the node's policy",
"edit_node_policy", {"pk": node.pk, "checker": <the alert's checker>})`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/_tests/views/test_node_policy.py apps/console/_tests/test_intents.py apps/alerts/_tests/test_describing_reeval.py -q`
Expected: FAIL with `NoReverseMatch: 'node-policy' is not a valid view function or pattern name`.

**Step 3: Implement**

Append to `apps/console/queries/node.py`. `EditorGroup` has fields: label, anchor, fields (a
tuple of `forms.BoundField`). `PolicyEditor` has fields: pk, name, form, groups, add (a
`forms.BoundField | None`), digest, node_url, url.

```python
def build_policy_editor(pk: int, data=None, *, digest: str | None = None) -> PolicyEditor | None:
    """The editor for one node. ``digest`` keeps the one an invalid submit carried."""
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    form = NodePolicyForm(data, instance=node)
    return PolicyEditor(
        pk=node.pk,
        name=node_name(node.hostname),
        form=form,
        groups=tuple(
            EditorGroup(checker_label(checker), editor_anchor(checker), tuple(fields))
            for checker, fields in form.policy_groups()
        ),
        add=form[ADD_SECTION_FIELD] if ADD_SECTION_FIELD in form.fields else None,
        digest=config_digest(node.config) if digest is None else digest,
        node_url=reverse("console:node", args=[node.pk]),
        url=reverse("console:node-policy", args=[node.pk]),
    )
```

`apps/console/views/node_policy.py`:

```python
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.forms import ADD_SECTION_FIELD
from apps.alerts.node_policy import StalePolicy, editor_anchor, save_policy, scoring_changed
from apps.checkers.checkers import checker_label
from apps.console.queries.node import PolicyEditor, build_policy_editor
from apps.console.views.base import Notice
from apps.console.views.node import CHANGE_REQUIRED, NodePageView

STALE = "This node's policy changed since you opened it. Here it is now."
SCORING_CHANGED = (
    "This policy doesn't touch the alerts already open. "
    "Here is what re-evaluating them would do."
)
SAVED = "Policy saved."


class NodePolicyView(NodePageView):
    """Edit one node's policy. The rules live in ``NodePolicyForm`` and ``save_policy``."""

    template_name = "console/node_policy.html"
    partial_template_name = "console/partials/_node_policy.html"

    def landing_path(self) -> str:
        return reverse("console:node-policy", args=[self.kwargs["pk"]])

    def render_editor(
        self, editor: PolicyEditor | None, notice: Notice | None = None, status: int = 200
    ):
        if editor is None:
            return self.not_found()
        context = self.get_context_data(editor=editor, nav="nodes", notice=notice)
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        if not self.can_change:
            return HttpResponseForbidden(CHANGE_REQUIRED)
        return self.render_editor(build_policy_editor(kwargs["pk"]))

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return HttpResponseForbidden(CHANGE_REQUIRED)
        pk = kwargs["pk"]
        digest = request.POST.get("digest", "")
        editor = build_policy_editor(pk, request.POST, digest=digest)
        if editor is None or not editor.form.is_valid():
            return self.render_editor(editor)
        config = editor.form.policy_config
        try:
            stored = save_policy(pk, config, expected=digest)
        except StalePolicy:
            return self.render_editor(build_policy_editor(pk), Notice(STALE, "warning"), 409)
        if scoring_changed(stored, config):
            # The preview's GET, never its apply: a mistyped threshold must not resolve
            # incidents unseen.
            messages.info(request, SCORING_CHANGED)
            return HttpResponseRedirect(reverse("console:node-reeval", args=[pk]), status=303)
        added = editor.form.cleaned_data.get(ADD_SECTION_FIELD)
        if added:
            messages.success(request, f"Opened the {checker_label(added)} policy boxes.")
            return HttpResponseRedirect(f"{self.landing_path()}#{editor_anchor(added)}", status=303)
        messages.success(request, SAVED)
        return HttpResponseRedirect(reverse("console:node", args=[pk]), status=303)
```

The redirect needs `console:node-reeval`. Task 7 builds that page, so `views/node_reeval.py`
starts as a stub, the way Task 4 stubbed the node page: `class NodeReevalView(NodePageView)`
whose `get` returns `self.not_found()`. Routes:

```python
    path("nodes/<int:pk>/policy/", NodePolicyView.as_view(), name="node-policy"),
    path("nodes/<int:pk>/reevaluate/", NodeReevalView.as_view(), name="node-reeval"),
```

Intent, importing `editor_anchor` from `apps.alerts.node_policy`:

```python
def _node_policy(params: dict[str, Any]) -> str:
    url = reverse("console:node-policy", args=[params["pk"]])
    anchor = editor_anchor(str(params.get("checker", "")))
    return f"{url}#{anchor}" if anchor else url
```

`"edit_node_policy": Intent(_node_policy, "get", "alerts.change_node")`.

`apps/alerts/describing.py`, in `_describe_skip`:

```python
    if node is not None and skip.reason in EDITOR_FIXABLE:
        params = {"pk": node.pk, "checker": _checker_name(skip.alert)}
        action = Action("Edit the node's policy", intent="edit_node_policy", params=params)
```

`console.css` gains the form component after `c-search`. The box rules copy
`.c-search__input`:

```css
.c-form__group { margin: 0 0 var(--space-3); padding: 0; min-width: 0; }
.c-field { display: grid; gap: var(--space-1); margin-bottom: var(--space-3); }
.c-field__label { font-weight: 600; }
.c-field input,
.c-field select {
  max-width: 24rem;
  padding: 3px 12px;
  border: 1px solid var(--borderColor-default);
  border-radius: var(--radius);
  background: var(--bgColor-default);
  color: var(--fgColor-default);
  font: 14px/20px var(--fontStack-sans);
}
.c-field input:focus-visible,
.c-field select:focus-visible { border-color: var(--focus-outlineColor); outline-offset: -1px; }
.c-field__help { margin: 0; color: var(--fgColor-muted); font-size: 12px; }
.c-field__error { margin: 0; }
```

`partials/_field.html`. Django points a box's `aria-describedby` at `<id>_helptext`, so the help
carries that id:

```html
<div class="c-field">
  <label class="c-field__label" for="{{ field.id_for_label }}">{{ field.label }}</label>
  {{ field }}
  {% if field.help_text %}<p id="{{ field.auto_id }}_helptext" class="c-field__help">{{ field.help_text }}</p>{% endif %}
  {% for error in field.errors %}<p class="c-field__error c-tone-critical">{{ error }}</p>{% endfor %}
</div>
```

`partials/_node_policy.html`:

```html
{% if notice %}{% include "console/partials/_flash.html" with tone=notice.tone text=notice.text %}{% endif %}
<a id="policy-back" class="c-crumb" href="{{ editor.node_url }}">{% include "console/partials/_octicon.html" with name="chevron-right" %}Back to the node</a>
<div class="c-pagehead"><div class="c-pagehead__top"><h1 class="c-pagehead__title">Policy for {{ editor.name }}</h1></div></div>
<form method="post" action="{{ editor.url }}" data-disable-on-submit>
  {% csrf_token %}
  <input type="hidden" name="digest" value="{{ editor.digest }}">
  {% for group in editor.groups %}
  <fieldset id="{{ group.anchor }}" class="c-box c-form__group">
    <legend class="c-box__header c-box__title">{{ group.label }}</legend>
    <div class="c-box__body">{% for field in group.fields %}{% include "console/partials/_field.html" %}{% endfor %}</div>
  </fieldset>
  {% empty %}
  <p class="c-muted">This node reports no checker that takes a policy yet.</p>
  {% endfor %}
  {% if editor.add %}
  <div id="policy-add" class="c-box c-form__group"><div class="c-box__body">{% include "console/partials/_field.html" with field=editor.add %}</div></div>
  {% endif %}
  <button id="policy-save" class="c-button c-button--primary" type="submit">Save policy</button>
  <a id="policy-cancel" class="c-button" href="{{ editor.node_url }}">Cancel</a>
</form>
```

`node_policy.html` sets the title to `Policy · {{ editor.name }} · Console`, then includes
`_messages.html` and the partial. The editor doesn't poll, because a poll would wipe what was
typed.

**Step 4: Run them**

Run: `uv run pytest apps/alerts/ apps/console/ -q`
Expected: PASS. `test_alert_reeval.py::test_a_skip_row_links_the_policy_editor` still passes,
now through `edit_node_policy`.

**Step 5: Commit**

```bash
git add apps/alerts apps/console templates/console static/console/console.css
git commit -m "feat(console): edit a node's policy, refusing a stale save"
```

---

### Task 7: Re-evaluate a node

**Files:**
- Modify: `apps/console/queries/reeval.py`, `apps/console/views/node_reeval.py`,
  `apps/console/intents.py`
- Test: `apps/console/_tests/queries/test_reeval.py`, `apps/console/_tests/views/test_node_reeval.py`,
  `apps/console/_tests/views/test_node_policy.py`, `apps/console/_tests/views/test_node.py`,
  `apps/console/_tests/test_intents.py`

**Step 1: Write the failing tests**

`queries/test_reeval.py`, with `make_reevaluable_alert` from `apps.alerts._tests.fixtures`, a
firing critical CPU alert at 85 under an 80/99 policy on node `web-03`:
- `test_a_missing_node_is_none`.
- `test_the_node_preview_names_each_alert`: one row whose headline starts with
  `alert_name(alert)`, `can_apply`, `title == "Re-evaluate the open alerts on web-03"`,
  `back_url` the node page, and "Applying queues 1 pipeline run." in `notes`.
- `test_a_checker_narrows_even_when_it_matches_nothing`: `checker="memory"` gives no rows,
  `empty == f"No open {checker_label('memory')} alerts on web-03."`, no `can_apply`, and a `url`
  ending `?checker=memory`.
- `test_an_empty_node_wide_scope_says_nothing_needs_it`: the alert resolved first gives
  `empty == "No open alerts need re-evaluation."`.
- `test_the_digest_covers_the_policy`: a config change changes `digest`.

`views/test_node_reeval.py`, modelled on `test_alert_reeval.py`, with:

```python
def _url(node, checker=""):
    url = reverse("console:node-reeval", args=[node.pk])
    return f"{url}?checker={checker}" if checker else url


def _digest(node, checker=""):
    return scope_digest(node_scope(node, checker))
```

- The route is `/console/nodes/7/reevaluate/`, and a GET writes nothing.
- `node_viewer` gets the preview with no `id="reeval-apply"`. Its POST is a 403 and writes no
  `AlertHistory`.
- `test_apply_writes_history_enqueues_and_redirects`: a 303 to the node page, a `PipelineRun` on
  the alert's incident, and "Resolved 0, changed severity on 1" on the followed page.
- `test_a_checker_scope_applies_only_that_checker`: a second firing memory alert under a memory
  policy that would re-score it. POST `?checker=cpu` with the cpu digest. The memory alert keeps
  its severity.
- `test_a_stale_digest_refuses_with_a_fresh_preview`: a 409, the stale notice, no history.
- `test_a_double_submit_applies_once`: two POSTs with one digest give 303 then 409, and one
  `AlertHistory` row.
- `test_a_busy_database_is_a_409`: patch `apps.console.views.node_reeval.apply_reeval` with
  `side_effect=OperationalError("database is locked")`.
- An empty scope offers no apply. A missing node is the console 404 page on GET and POST.

`test_node_policy.py`: the scoring-change test follows the redirect and finds "This policy
doesn&#x27;t touch the alerts already open.". `test_node.py` gains
`test_an_operator_sees_both_actions`: `id="action-edit_node_policy"` and
`id="action-reevaluate_node"`. `test_intents.py`: `("reevaluate_node", {"pk": 7},
"/console/nodes/7/reevaluate/", "get")` and `("reevaluate_node", {"pk": 7, "checker": "disk
inodes"}, "/console/nodes/7/reevaluate/?checker=disk+inodes", "get")`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/ -q`
Expected: FAIL with `ImportError: cannot import name 'node_scope'`.

**Step 3: Implement**

Append to `apps/console/queries/reeval.py`, importing `urlencode`, `reverse`, `Node`,
`checker_label` and `node_name`:

```python
NOTHING_OPEN = "No open alerts need re-evaluation."


def node_scope(node: Node, checker: str) -> ReevalScope:
    """One checker when named, else the whole node.

    A named checker narrows even when it matches nothing: a stale link widened to the whole
    node would re-evaluate, and notify about, alerts nobody asked about.
    """
    return ReevalScope.for_checker(node, checker) if checker else ReevalScope.for_node(node)


def build_node_reeval_preview(pk: int, checker: str = "") -> ReevalPreview | None:
    node = Node.objects.filter(pk=pk).first()
    if node is None:
        return None
    name = node_name(node.hostname)
    url = reverse("console:node-reeval", args=[node.pk])
    if checker:
        label = checker_label(checker)
        title = f"Re-evaluate the {label} alerts on {name}"
        empty = f"No open {label} alerts on {name}."
        url += f"?{urlencode({'checker': checker})}"
    else:
        title, empty = f"Re-evaluate the open alerts on {name}", NOTHING_OPEN
    return reeval_preview(
        node_scope(node, checker),
        title=title,
        back_url=reverse("console:node", args=[node.pk]),
        back_text="Back to the node",
        url=url,
        named=True,
        empty=empty,
    )
```

`apps/console/views/node_reeval.py` replaces the stub:

```python
from urllib.parse import urlencode

from django.contrib import messages
from django.db import OperationalError
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse

from apps.alerts.describing import describe_reeval_applied
from apps.alerts.models import Node
from apps.alerts.reeval_existing import StaleReeval, apply_reeval
from apps.console.filters import checker_param
from apps.console.queries.reeval import build_node_reeval_preview, node_scope
from apps.console.views.base import Notice
from apps.console.views.node import CHANGE_REQUIRED, NodePageView

STALE = (
    "This node's alerts or policy changed since you looked. "
    "Here is what re-evaluating does now."
)


class NodeReevalView(NodePageView):
    """Preview, then apply, a node's re-score. The rules live in ``apply_reeval``."""

    template_name = "console/reeval.html"
    partial_template_name = "console/partials/_reeval.html"

    @property
    def checker(self) -> str:
        return checker_param(self.request.GET)

    def landing_path(self) -> str:
        url = reverse("console:node-reeval", args=[self.kwargs["pk"]])
        return f"{url}?{urlencode({'checker': self.checker})}" if self.checker else url

    def render_preview(self, notice: Notice | None = None, status: int = 200):
        preview = build_node_reeval_preview(self.kwargs["pk"], self.checker)
        if preview is None:
            return self.not_found()
        context = self.get_context_data(
            preview=preview, can_reeval=self.can_change, nav="nodes", notice=notice
        )
        return self.render_to_response(context, status=status)

    def get(self, request, *args, **kwargs):
        return self.render_preview()

    def post(self, request, *args, **kwargs):
        if not self.can_change:
            return HttpResponseForbidden(CHANGE_REQUIRED)
        node = Node.objects.filter(pk=kwargs["pk"]).first()
        if node is None:
            return self.not_found()
        try:
            report = apply_reeval(
                node_scope(node, self.checker), expected=request.POST.get("digest", "")
            )
        except (StaleReeval, OperationalError):
            return self.render_preview(Notice(STALE, "warning"), status=409)
        messages.success(request, describe_reeval_applied(report))
        return HttpResponseRedirect(reverse("console:node", args=[node.pk]), status=303)
```

The apply form posts to `preview.url`, which carries `?checker=`, so the POST keeps its scope.
The intent:

```python
def _node_reeval(params: dict[str, Any]) -> str:
    url = reverse("console:node-reeval", args=[params["pk"]])
    checker = params.get("checker")
    return f"{url}?{urlencode({'checker': checker})}" if checker else url
```

`"reevaluate_node": Intent(_node_reeval, "get", "alerts.change_node")`.

**Step 4: Run them**

Run: `uv run pytest apps/console/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add apps/console
git commit -m "feat(console): re-evaluate a node's open alerts"
```

---

### Task 8: The fleet policy page

**Files:**
- Create: `apps/console/queries/policy.py`, `apps/console/views/policy.py`,
  `templates/console/policy.html`, `templates/console/partials/_policy.html`
- Modify: `apps/console/views/__init__.py`, `apps/console/urls.py`, `apps/console/intents.py`
- Test: `apps/console/_tests/queries/test_policy.py`, `apps/console/_tests/views/test_policy.py`,
  `apps/console/_tests/test_intents.py`

**Step 1: Write the failing tests**

`queries/test_policy.py` on `build_policy_page()`:
- A node with a half-filled pair comes before a healthy one.
- `test_rows_read_in_labels_with_the_keys_kept_for_details`: config `{"listening_ports":
  {"allowlist": [22], "extra_key": 1}}` gives a row with `checker ==
  checker_label("listening_ports")`, `key == "listening_ports"` and `unread == (Fact("Stored
  key", "listening_ports → extra_key"),)`.
- A node with no hostname is "Unnamed node", and its group's `facts` hold the instance ID.
- Each row's `edit` and `reeval` equal its `PolicyRow`'s.
- `quiet_count` counts the nodes with no policy and nothing firing.

`views/test_policy.py`: the route `/console/policy/`, anonymous, `bare_staff` 403, `node_viewer`
reads, and the nav marks Nodes current. `test_a_reader_gets_no_edit_or_reevaluate_links`: a
firing CPU alert under a CPU policy. `node_viewer`'s body holds neither
`/console/nodes/<pk>/policy/#policy-cpu` nor `/console/nodes/<pk>/reevaluate/?checker=cpu`, and
`node_operator`'s holds both.

`test_intents.py`: `("view_policy", {}, "/console/policy/", "get")`, and `("view_policy", {},
"alerts", "node")` in the view-or-change test.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/console/ -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.console.queries.policy'`.

**Step 3: Implement**

`apps/console/queries/policy.py`, docstring "Read model for the fleet policy page. Returns
dataclasses, never querysets." `PolicyTableRow` has fields: key, checker, policy, status, tone,
why, effect, applied, caution, edit, reeval, unread. `PolicyGroup` has fields: pk, name,
has_problem, view, rows, facts. `PolicyPage` has fields: groups, quiet_count.

```python
def _row(row: PolicyRow) -> PolicyTableRow:
    return PolicyTableRow(
        key=row.checker,
        checker=checker_label(row.checker),
        policy=row.policy,
        status=row.status,
        tone=row.tone,
        why=row.why,
        effect=row.effect,
        applied=row.applied,
        caution=row.caution,
        edit=row.edit,
        reeval=row.reeval,
        unread=tuple(Fact("Stored key", label) for label in row.unread),
    )


def build_policy_page() -> PolicyPage:
    overview = build_policy_overview()
    return PolicyPage(
        groups=tuple(
            PolicyGroup(
                pk=group.pk,
                name=node_name(group.hostname),
                has_problem=group.has_problem,
                view=Action("Open the node", "view_node", {"pk": group.pk}),
                rows=tuple(_row(row) for row in group.rows),
                facts=(Fact("Instance ID", group.instance_id, copy=True),),
            )
            for group in overview.groups
        ),
        quiet_count=overview.quiet_count,
    )
```

`apps/console/views/policy.py` holds `PolicyView(ConsoleView)` with `console/policy.html`,
`console/partials/_policy.html` and `read_permissions = NODE_READ`. Its `get_context_data` adds
`page=build_policy_page()` and `nav="nodes"`. Route `path("policy/", PolicyView.as_view(),
name="policy")`. Intent `"view_policy": Intent(_page("console:policy"), "get", NODE_READ)`.

Templates. `policy.html` has the h1 "Fleet policy" and includes the partial. `_policy.html`
renders one `c-box` per group, `id="policy-node-{{ group.pk }}"`. The header is the group's
name, linked through `group.view` as `policy-node-{{ group.pk }}-open`, plus `_details.html`
with `facts=group.facts` and an id built with `{% with %}` from the pk. Then a `c-table`:
Checker, Policy, Status (`c-label c-tone-{{ row.tone }}`), Why (in `c-tone-warning` when
`row.caution`), Effect, Last applied, and an actions cell. The cell resolves `row.edit` and
`row.reeval` and renders each only when it has a URL, as `policy-{{ group.pk }}-{{ row.key
}}-edit` and `-reeval`. A row with `unread` adds `_details.html` with `facts=row.unread` in its
Why cell. After the groups: "{{ page.quiet_count }} node{{ page.quiet_count|pluralize }} hold
no policy and fire nothing." when nonzero. With no groups: "No node holds a policy yet.". The
page doesn't poll.

**Step 4: Run them**

Run: `uv run pytest apps/console/ -q`
Expected: PASS. The node list's Fleet policy link now resolves.

**Step 5: Commit**

```bash
git add apps/console templates/console
git commit -m "feat(console): the fleet policy page"
```

---

### Task 9: Point the dashboard at the console

**Files:**
- Modify: `config/dashboard.py`, `templates/admin/dashboard.html`
- Test: `config/_tests/test_dashboard.py`, `config/_tests/test_dashboard_render.py`,
  `config/_tests/test_dashboard_readiness.py`

**Step 1: Update the tests**

- `test_dashboard.py:156`: `row.url == reverse("console:node", args=[node.pk])`.
- `test_dashboard_render.py:25`: `reverse("console:policy") in body`.
- `test_dashboard_readiness.py` gains: the nodes entry's `url` is `reverse("console:nodes")`.

**Step 2: Run them to see them fail**

Run: `uv run pytest config/_tests/test_dashboard.py config/_tests/test_dashboard_render.py config/_tests/test_dashboard_readiness.py -q`
Expected: FAIL on the old admin URLs.

**Step 3: Implement**

- `config/dashboard.py:240`: `"url": reverse("console:nodes")`.
- `config/dashboard.py:579`: `url=reverse("console:node", args=[node.pk])`.
- `templates/admin/dashboard.html:8` and `:179`: `{% url 'console:policy' %}`, link text "Fleet
  policy".

**Step 4: Run them**

Run: `uv run pytest config/ -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add config templates/admin/dashboard.html
git commit -m "feat: the dashboard opens nodes and policy in the console"
```

---

### Task 10: The guard test covers nodes and policy

**Files:**
- Modify: `apps/console/_tests/fixtures.py`, `apps/console/_tests/test_guard.py`

**Step 1: Extend the world**

```python
def _policy_incident() -> Incident:
    """A peer whose policy is in effect, not scoring and partly unread, with a CPU alert the
    policy re-scores, beside a node that never sent a hostname."""
    node = Node.objects.create(
        instance_id=_uuid(),
        hostname="cache-1",
        address="10.0.0.7",
        labels={"rack_unit": "r1"},
        config={
            "cpu": {"warning_threshold": 80, "critical_threshold": 99, "warn_level": 3},
            "disk_inodes": {"warning_threshold": 70},
            "listening_ports": {"allowlist": [22, 70000]},
            "gpu_fans": {"max_rpm": 900},
        },
    )
    unnamed = Node.objects.create(instance_id=_uuid())
    # Old, so the readiness card still reads "No node reported in the last 15 minutes".
    Node.objects.filter(pk__in=[node.pk, unnamed.pk]).update(
        last_seen=timezone.now() - timedelta(hours=2)
    )
    incident = _incident("Cache host", instance=node.instance_id, node=node)
    for checker, severity, annotations in (
        ("cpu", "critical", {"metrics": json.dumps({"cpu_percent": 85.0})}),
        ("listening_ports", "warning", {}),
    ):
        _alert(
            incident,
            checker,
            source="cluster",
            node=node,
            severity=severity,
            labels={"checker": checker, "instance_id": node.instance_id},
            annotations=annotations,
        )
    return incident
```

Add `_policy_incident()` to `build_world`'s list. `World` gains `node_pks`, read with
`Node.objects.values_list("pk", flat=True)` at the end of `build_world`, and `policy_node_pk`,
the Cache host incident's `node_id`.

**Step 2: Extend the guard test**

In `test_no_page_leaks_identifiers`, add:

```python
        + ["/console/nodes/", "/console/nodes/?freshness=silent", "/console/policy/"]
        + [f"/console/nodes/{pk}/" for pk in world.node_pks]
        + [f"/console/nodes/{pk}/policy/" for pk in world.node_pks]
        + [f"/console/nodes/{pk}/reevaluate/" for pk in world.node_pks]
        + [f"/console/nodes/{world.policy_node_pk}/reevaluate/?checker=listening_ports"]
```

Add to `shown`: "Unnamed node", "Saved but not scoring", "Not honoured", "Edit policy",
"Re-evaluate open alerts", "Allowed ports", "would move this alert from critical to warning",
"No open alerts need re-evaluation." and "Applying queues 1 pipeline run.".

**Step 3: Run it**

Run: `uv run pytest apps/console/_tests/test_guard.py -q`
Expected: PASS. A failure names the page and the token. Fix the wording at its describer, query
or template, never in the guard.

**Step 4: Commit**

```bash
git add apps/console/_tests/fixtures.py apps/console/_tests/test_guard.py
git commit -m "test(console): the guard covers nodes and policy"
```

---

### Task 11: Cut the node admin and the dead code behind it

**Files:**
- Modify: `apps/alerts/admin.py`, `config/admin.py`, `apps/alerts/node_overview.py`,
  `apps/alerts/node_policy.py`, `apps/alerts/policy_overview.py`, `apps/alerts/reeval_existing.py`,
  `apps/alerts/forms.py` (docstrings), `config/settings.py`, `pyproject.toml`, `uv.lock`,
  `templates/admin/base_site.html` (comment)
- Delete: `templates/admin/alerts/node/`, `templates/admin/policy_overview.html`,
  `apps/alerts/_tests/test_node_admin.py`, `config/_tests/test_policy_overview_view.py`,
  `config/_tests/test_admin_object_actions.py`
- Test: `apps/alerts/_tests/test_admin.py`, `apps/alerts/_tests/test_node_overview.py`,
  `apps/alerts/_tests/test_node_policy.py`, `apps/alerts/_tests/test_policy_overview.py`,
  `apps/alerts/_tests/test_reeval_existing.py`, `config/_tests/test_admin_site.py`,
  `config/_tests/test_dashboard_render.py`, `config/_tests/test_no_inline_colour.py`

**Step 1: Write the failing tests**

Add to `apps/alerts/_tests/test_admin.py`, next to `TestAlertAdminIsStock`:

```python
@pytest.mark.django_db
class TestNodeAdminIsStock:
    def test_read_only_with_no_actions(self, rf, admin_user):
        model_admin = admin.site._registry[Node]
        request = rf.get("/")
        request.user = admin_user
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
        assert not model_admin.get_actions(request)
        assert not hasattr(model_admin, "change_actions")

    def test_the_change_page_still_opens(self, admin_client):
        node = Node.objects.create(instance_id="n1", hostname="n1")
        url = reverse("admin:alerts_node_change", args=[node.pk])
        assert admin_client.get(url).status_code == 200


@pytest.mark.django_db
def test_the_admin_policy_page_is_gone(admin_client):
    assert admin_client.get("/admin/policy/").status_code == 404
```

Delete or retarget the tests of what goes:
- Delete `test_node_admin.py`, `test_policy_overview_view.py` and `test_admin_object_actions.py`.
- `test_node_overview.py`: delete `SeverityChipTests`, `RecentIncidentTests` and
  `PipelinePanelTests`. The two `build_node_overview(node).checker_time_label` tests call
  `checker_time_label(node)`. Drop the `CheckerRow.url` assertion at line 220.
- `test_node_policy.py`: delete the `editor_link` and `PolicySection.anchor` tests (lines 790 to
  836). Expect "the editor is stricter than the scorers" wherever "the boxes below are stricter"
  is pinned.
- `test_policy_overview.py`: delete every `edit_url`, `reeval_url` and `node_url` assertion and
  the `_change_url` helper. The editor-note `why` ends "Retyping it in the editor means changing
  it.".
- `test_reeval_existing.py`: delete the `fix_url` tests (from line 1020).
  `test_describing_reeval.py` covers the fix through the skip's action.
- `test_admin_site.py`: the link tests use "Network map" and `/admin/map/`. A `view_node` user
  gets no section links. A `view_pipelinedefinition` user gets `{"Configuration": ["Network
  map"]}`.
- `test_dashboard_render.py`: drop the `object-tools` assertion and its docstring clause, and
  `admin:policy-overview` from the rendered surfaces.
- `test_no_inline_colour.py`: drop `templates/admin/policy_overview.html` and
  `templates/admin/alerts/node/change_form.html` from `MUST_BE_SCANNED`.

**Step 2: Run them to see them fail**

Run: `uv run pytest apps/alerts/_tests/test_admin.py -q`
Expected: FAIL on `has_change_permission` and on the `/admin/policy/` 200.

**Step 3: Implement**

`apps/alerts/admin.py`:

```python
@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    """Read-only. Operators read nodes and edit their policy in the console."""

    list_display = ["instance_id", "hostname", "last_source", "first_seen", "last_seen"]
    search_fields = ["instance_id", "hostname"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

Let ruff list the imports that go: `PermissionDenied`, `Count`, `Q`, `HttpResponseRedirect`,
`TemplateResponse`, `reverse`, both `django_object_actions` names, and the `forms`,
`node_overview`, `node_policy` and `reeval_existing` imports.

`config/admin.py`: delete `policy_view`, its `path`, `SECTION_LINKS["Operations"]`, and the
`Node` and `build_policy_overview` imports. `map_view`'s docstring stops pointing at
`policy_view`.

`apps/alerts/node_overview.py`: delete `SEVERITIES_WORST_FIRST`, `UNRESOLVED_INCIDENT_STATUSES`,
`unresolved_counts`, `render_severity_chips`, `IncidentRow`, `build_incident_rows`,
`PipelineRow`, `build_pipeline_rows`, `NodeOverview`, `build_node_overview` and
`CheckerRow.url`. The module docstring says the console node page reads these panels.

`apps/alerts/node_policy.py`: delete `EditorLink`, `editor_link` and `PolicySection.anchor`.
`_editor_note` returns `f"the editor is stricter than the scorers, and {reason}"`. Docstrings
that name `NodeAdmin` or "the boxes below" name the console editor.

`apps/alerts/policy_overview.py`: delete `edit_url`, `reeval_url`, `NodeGroup.node_url`,
`_node_url`, `_edit_url`, `_reeval_url` and their imports. In `_why`, "Retyping it on the node
page means changing it." becomes "Retyping it in the editor means changing it.".

`apps/alerts/reeval_existing.py`: delete `AlertSkip.fix_url` and the `admin_url` import. The
module docstring says "Operator-triggered (the console and a management command)", and the two
`*_node_alert_reeval` wrappers are "kept for the command".

`apps/alerts/forms.py`: the `Meta` comment and the `_sections_for_instance` docstring stop
naming the admin. The code stays.

Delete `templates/admin/alerts/node/` and `templates/admin/policy_overview.html`. Then the
dependency:

```bash
uv remove django-object-actions
```

Delete `"django_object_actions",` from `INSTALLED_APPS` in `config/settings.py`, and the
`django_object_actions` clause from the comment in `templates/admin/base_site.html`. Then:

```bash
grep -rn "object_actions\|change_actions\|render_severity_chips\|editor_link\|fix_url\|policy-overview\|policy_view\|build_node_overview" apps config templates
```

Expected: no output.

**Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. The `SECTION_MAP` completeness test still passes, because `alerts.node` stays
mapped.

**Step 5: Commit**

```bash
git add -A apps/alerts apps/console config templates pyproject.toml uv.lock
git commit -m "feat!: the node admin is stock and read-only

BREAKING CHANGE: the node policy boxes, Re-evaluate open alerts and
/admin/policy/ are gone from the admin. They live on the console node,
editor, re-evaluate and fleet policy pages. django-object-actions is
no longer a dependency."
```

---

### Task 12: Docs

**Files:**
- Modify: `AGENTS.md`, `apps/console/AGENTS.md`, `apps/alerts/AGENTS.md`, `apps/checkers/AGENTS.md`,
  `docs/Architecture.md`

**Step 1: Edit**

`AGENTS.md`, rule 3: "Incidents, alerts, runs, the inbox, nodes and node policy are read and
acted on in the console (`/console/`). Their admins are stock and read-only."

`apps/console/AGENTS.md`, under "Rules":
- **The node pages read with `alerts.view_node` or `change_node`.** `/console/nodes/` filters on
  `NodeFilter` (`q` on hostname and instance ID, `freshness` all, silent or reporting) and sorts
  worst first by the Overview tile's rule. Its counts come from `open_incidents_by_node`, which
  follows `on_node`, so a count and the incident list it opens agree. A node with no hostname
  reads "Unnamed node". Its instance ID lives in Details.
- **The editor and re-evaluate need `alerts.change_node`.** A reader gets a 403 on the editor,
  and the preview with no apply. The editor is a plain form that doesn't poll. It carries
  `config_digest` of the stored policy, and `node_policy.save_policy` refuses a stale one with a
  409 and fresh boxes. It never calls `form.save()`, because that bumps `last_seen`.
- **Both re-evaluate pages share `queries/reeval.py` and `partials/_reeval.html`.** A node
  preview names each alert and states the run count and the sweep before the apply.
  `?checker=` narrows the scope even when it matches nothing.
- **Fleet policy** (`/console/policy/`) renders `build_policy_overview`. Its edit and
  re-evaluate links are intents, so a reader sees none.

`apps/alerts/AGENTS.md`: "Node detail page" says `node_overview.py` feeds the console node page.
"Node policy form" says the console editor renders `NodePolicyForm` through `policy_groups()`
and saves through `save_policy`. The memo paragraph drops its `NodeAdmin.get_fieldsets`
reasoning: the form and the node page each ask. "Re-evaluation surfaces" names
`/console/policy/` and `/console/nodes/<pk>/reevaluate/`, and drops the `has_change_permission`
bullet. Line 65: the wrappers are kept for the command.

`apps/checkers/AGENTS.md:34`: the sparkline draws the console node page's charts.

`docs/Architecture.md:218`: "Hub-side policy" becomes "Fleet policy" at `/console/policy/`.
Access is `alerts.view_node` or `change_node`. The button scopes the console re-evaluate page to
one checker.

**Step 2: Verify everything**

```bash
uv run black . --check && uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report --include="apps/console/*,apps/alerts/node_policy.py,apps/alerts/forms.py,apps/alerts/describing.py,apps/alerts/policy_overview.py,apps/alerts/node_overview.py,apps/alerts/reeval_existing.py,apps/alerts/admin.py,config/admin.py,config/dashboard.py"
uv run bandit -r apps/ config/ -c pyproject.toml
uv run pip-audit --strict --desc
uv run python manage.py check
```

Expected: all green, with 100% branch coverage on the changed files. Then, against a database
with no active channel, open `/console/nodes/`, a peer, its editor and its re-evaluate page in a
browser. Save a threshold, land on the preview, apply, and check the notice on the node page.
Open the editor in two tabs and save both: the second gets the 409. `console.js` has no
automated test.

**Step 3: Commit**

```bash
git add AGENTS.md apps/console/AGENTS.md apps/alerts/AGENTS.md apps/checkers/AGENTS.md docs/Architecture.md
git commit -m "docs: nodes and policy live in the console"
```

{% endraw %}
