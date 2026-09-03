---
title: "Node policy form"
parent: Plans
---

{% raw %}

# Node policy form

## Problem

`Node.config` is the hub's per-node policy: it lets one machine's disk run hot
without paging, and one machine's open port be expected rather than a finding.
It is edited through a raw JSON textarea, and it silently honours exactly three
shapes:

- `warning_threshold` and `critical_threshold`, per numeric checker
- `allowlist`, for `listening_ports`

Nothing on the page says so. Nothing lists which checkers accept policy. Nothing
validates what you type.

That last one is the sharp edge, because of how `apps/alerts/reevaluation.py`
is built. It runs inside the ingest path, so it **must never raise on a node's
push**. Every scorer returns `None` — passthrough, keep the node's own verdict —
for any input it cannot use: a non-dict config, a missing metric, a string where
a number belongs, and notably a `critical_threshold` below `warning_threshold`
(`reevaluation.py:60`).

The consequence: **a typo'd policy is indistinguishable from no policy.** An
operator sets `critical_threshold: 80` and `warning_threshold: 90`, saves, sees
no error, and the policy does nothing. Forever. That is the complaint.

## What is already right, and stays

The fail-open contract is correct and is not being changed. A node pushing
malformed data must not take the ingest path down. The problem is not that the
runtime is lenient; it is that **the keyboard is equally lenient**, and it has no
reason to be. A human typing a threshold into an admin form can be told they are
wrong, immediately.

So: strict at the keyboard, fail-open at ingest. Same rules, opposite posture.

## Storage: no change

Policy stays a JSON blob on `Node.config`. A relational `NodeCheckerPolicy`
model was considered and rejected: it costs a migration, a backfill of every
existing config, and a rewrite of how the scorers read policy — inside the ingest
path, the one place in this system where a mistake is most expensive. The
storage was never the complaint. The missing structure and validation live in the
editor, and that is where they are being added.

No migration. Every existing config keeps working.

## Design

### Schema derived, never hand-listed

A new `apps/alerts/node_policy.py` builds the field spec from
`reevaluation.SCORERS` and `reevaluation.PRIMARY_METRIC`:

| Checker | Fields |
|---|---|
| the seven in `PRIMARY_METRIC` | `warning_threshold`, `critical_threshold` |
| `listening_ports` | `allowlist` |

Add a scorer and the form grows a section on its own. This is the same
one-authority move that folded the duplicated `CHECKER_PRIMARY_METRIC` away in
PR #230: a mapping that exists twice is a mapping that will disagree with itself.

A test asserts a new `SCORERS` entry appears in the form without the form code
being touched.

### Validation mirrors the scorers, and refuses

Each rule the scorers silently tolerate becomes a field error:

- a non-numeric threshold
- `critical_threshold` below `warning_threshold` (`_score_numeric` treats this as
  malformed and passes through)
- a non-integer or non-list `allowlist` entry (`_int_set` rejects bool too, since
  it is an `int` subclass)

The form reuses the scorers' own predicates rather than restating the rules, so
the two cannot drift into disagreeing about what a valid policy is.

### Which sections appear

The union of:

- the checkers this node actually reports — available from `build_checker_rows`,
  already computed for the page
- any checker its config already names

A checker that is configured but no longer reported still shows, so policy never
becomes invisible. A hub running fourteen checkers and a peer running three get
right-sized forms, and a disk threshold cannot be set on a machine that never
reports disk.

An "add policy for another checker" select covers the remainder of `SCORERS`.

### Unknown keys are preserved, and shown

The form edits the keys it understands and writes back everything else untouched.
Unknown keys render read-only with a note that no scorer reads them.

Two reasons. Nothing an operator authored is ever silently deleted — that is the
same class of surprise as the JSON widget. And a stale key, left behind by a
removed checker or a hand-edit, becomes visible instead of invisibly dead.

### Save shows consequences

Changing policy takes two steps today: save the config, then remember to click
"Re-evaluate open alerts". A saved policy that nobody re-evaluates does nothing
to the alerts already open.

A save that changed anything scoring-relevant now redirects to the existing
re-evaluate preview, which already reports what would resolve and what would
change severity. It reuses `preview_node_alert_reeval` and
`apply_node_alert_reeval` rather than building a second path. A save that changed
nothing scoring-relevant skips the redirect.

Still two deliberate acts. An admin save must not silently mutate alert state: a
fat-fingered threshold that resolves real incidents with no preview is a worse
failure than the one being fixed.

### Where it lives

Replaces the `JSONEditorWidget` on `config` in `NodeAdmin`, rendered in the
existing `templates/admin/alerts/node/change_form.html` below the overview panels
added in PR #230.

## Testing

`apps/alerts/_tests/test_node_policy.py`:

- spec derivation from `SCORERS`, including the it-grows-by-itself guard
- a config with no changes round-trips byte-identical
- unknown keys survive a save
- each validation rule, including inverted thresholds and a bool in an allowlist

Admin tests for section selection (reported, configured, both, neither) and for
the save-then-preview redirect, including the no-scoring-change case that must
not redirect.

100% branch coverage on changed code, per `AGENTS.md`.

## Out of scope

- Per-checker enable/disable, and disk mount exclusions. Both are new *policy
  capability*, not better editing of existing policy. They would extend
  `SCORERS`, and this form would then display them for free.
- Fleet-wide policy defaults. No current requirement.

{% endraw %}
