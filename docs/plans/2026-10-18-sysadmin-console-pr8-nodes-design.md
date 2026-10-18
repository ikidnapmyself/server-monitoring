---
title: "Sysadmin Console PR 8: Nodes and Policy Design"
parent: Plans
---

# Sysadmin console PR 8: nodes and policy

Domain slice 8 of `2026-10-15-sysadmin-console-foundation-design.md`. It moves nodes, the
per-node policy editor, node re-evaluation and the fleet policy table to the console, and
cuts `NodeAdmin` to stock.

## What exists

Nothing new is built below the pages. The rules already live outside the admin:

- `apps/alerts/node_overview.py`: identity and freshness, checker rows, open incidents,
  charts, preflight and recent runs for one node.
- `apps/alerts/node_policy.py`: the policy spec per checker, form values to config and
  back, `scoring_changed`, `sections_for`, `addable_checkers` and `build_effective_policy`.
- `apps/alerts/forms.py`: `NodePolicyForm`, which validates the boxes and writes `config`.
- `apps/alerts/reeval_existing.py`: `ReevalScope.for_node` and `for_checker`,
  `preview_reeval`, `apply_reeval(scope, expected=)` and `scope_digest`. PR 6 already
  uses the digest for one alert.
- `apps/alerts/policy_overview.py`: every node's policy as rows, with what each rule is
  doing to the open alerts.

The admin adds only rendering, `response_change`'s redirect and the permission checks.
Those move to console views.

## Pages

### `/console/nodes/`

Every node, worst first: the name, role (this hub or a peer), freshness, unresolved
incidents per severity and last seen. The tile tone and wording come from the same rule as
the Overview's node tiles, so a node reads the same on both pages. Chips: freshness (all,
silent, reporting) and `q` on hostname and instance ID. A header link opens the fleet
policy page.

### `/console/nodes/<pk>/`

- The headline: name, role, freshness and the severity counts.
- Checker state: one row per checker with its label, status, value and time.
- Open incidents as cards linking to the console incident page.
- Charts, as the admin draws them.
- Preflight: the last run's result. Its link stays on the admin until PR 10.
- Recent runs linking to the console run page.
- Policy in effect, then "Saved but not scoring" and "Not honoured", from
  `build_effective_policy`.
- Do this: Edit policy, and Re-evaluate open alerts, for a user who may change nodes.
- Details: instance ID, address, last source, labels and the raw `config`.

### `/console/nodes/<pk>/policy/`

The editor. It renders `NodePolicyForm`, one box group per checker in `sections_for`,
then the "Add a policy section" select when `addable_checkers` is non-empty. Each group
has an anchor, so the node page and the fleet table can link straight to one checker.

Save keeps the admin's outcomes:

- If `scoring_changed`, it lands on the node's re-evaluate preview with "This policy
  doesn't touch the alerts already open. Here is what re-evaluating them would do." It
  never applies.
- Else if a section was added, it lands back on the editor at that section.
- Else it lands on the node page with "Policy saved."

## Decision: the editor refuses a stale save

The admin let two editors overwrite each other's thresholds. The form now carries a
digest of the stored `config`, sorted JSON hashed like `scope_digest`. Save is one
conditional `UPDATE` on the row's pk and that config. If it loses, the page answers 409:
"This node's policy changed since you opened it. Here it is now." It shows the fresh
boxes, and nothing is written.

### `/console/nodes/<pk>/reevaluate/`

The preview and apply for node scope, or for one checker with `?checker=`. A present
`checker` narrows even when it matches nothing, as in the admin. The page reuses PR 6's
reeval partial: the report rows from `describe_reeval_report`, the reopen warning, and an
apply form carrying `scope_digest` with `data-disable-on-submit`.

Apply calls `apply_reeval(scope, expected=digest)`. A `StaleReeval` or `OperationalError`
answers 409 with the fresh preview, as in PR 6. Success redirects 303 to the node page with
`describe_reeval_applied`. A node-wide scope with no changes and no skips says "No open
alerts need re-evaluation." and offers no apply.

### `/console/policy/`

The fleet table from `build_policy_overview`: node, checker, the rule, its effect on the
open alerts, and when it last applied. Problem rows sort first. Each row links to that
node's editor anchor and to its checker-scoped re-evaluate.

## Describer contract

Checker names render through `checker_label`, never as `snake_case`. A node with no
hostname reads "Unnamed node" in the headline, and its instance ID appears only in Details.
`test_guard.py` covers the nodes list, every node page, every editor, a node and a checker
re-evaluate preview, and the fleet policy page.

## Permissions

- Read: `alerts.view_node` or `alerts.change_node`.
- Edit policy and re-evaluate: `alerts.change_node`. A reader gets the pages with no
  forms and no buttons.

## Wiring

- `view_nodes` and `view_node` point at the console. New intents `edit_node_policy` and
  `reevaluate_node`, both on `alerts.change_node`, and `view_policy` on view or change.
- `policy_overview._node_url`, `_edit_url` and `_reeval_url` point at the console.
- `config/dashboard.py` and `templates/admin/dashboard.html` link nodes and policy to the
  console.
- The sidebar gains Nodes after Inbox.

## Admin cut

- `NodeAdmin` becomes stock and read-only through `ReadOnlyAdmin`. `change_actions`,
  `reevaluate_open_alerts`, the form, fieldsets, `save_model`, `response_change`,
  `render_change_form` and the severity annotations go.
- `templates/admin/alerts/node/` goes, with `admin/policy_overview.html`,
  `MonitoringAdminSite.policy_view`, its URL and its `SECTION_MAP` entry.
- Helpers left with no caller go: `render_severity_chips`, `editor_link` if the console
  doesn't use it, and any admin-only formatting in `node_overview.py`.
- `NodePolicyForm` stays in `apps/alerts/forms.py`, now serving the console.
- The network map stays in the admin until PR 9.

## Tests

- Queries: the list order, chips and `q`, the node page for a hub, a peer, a silent peer
  and a node with no hostname, and the effective policy sections.
- The editor: each save outcome, an invalid box, the stale-save 409, and a reader's 403.
- Re-evaluate: node and checker scope, a stale digest's 409, an apply's redirect and
  notice, the empty node-wide scope, and a double submit applying once.
- The fleet table: row order and links.
- `test_guard.py` for every new page.
- Admin: `NodeAdmin` is stock and read-only, and `/admin/policy/` is gone.
- 100% branch coverage on changed files.

## Docs

- `apps/alerts/AGENTS.md`: the node admin section points at the console.
- `apps/console/AGENTS.md`: the node, editor, re-evaluate and policy pages and their
  permissions.
- `AGENTS.md`: nodes and policy join the list of things read and acted on in the console.

## Out of scope

- Per-checker enable and disable, and disk exclusions.
- The network map, which is PR 9.
- Preflight run pages, which belong to PR 10.
