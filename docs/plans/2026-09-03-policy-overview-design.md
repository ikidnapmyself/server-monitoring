---
title: "Hub-side policy overview page"
parent: Plans
---

# Hub-side policy overview page

**Status:** designed 2026-09-03, not yet implemented.

## Problem

Hub-side re-evaluation policy lives in `Node.config`, one JSON blob per node.
The Node change page explains that node's policy well: `build_effective_policy`
splits it into what scores, what is saved but scores nothing, and what nothing
reads at all. That panel is per node.

With eight nodes there is no way to answer "which machines have I overridden,
and is any of it silently doing nothing?" without opening every node in turn.
An override that fails open looks exactly like no override, so the failure mode
this page exists for is invisible by construction.

## Non-goals

- No editing. `NodePolicyForm` stays the only writer for `Node.config`.
- No re-evaluation action. The button on the Node change page stays the one
  entrypoint, per the single-entrypoint rule in `AGENTS.md`.
- No new storage, endpoint, or config surface. Everything here reads what the
  Node admin already reads.

## Placement

New module `apps/alerts/policy_overview.py`, mirroring `apps/alerts/node_overview.py`.
It exposes `build_policy_overview()` and owns nothing but presentation.

The view lives on `MonitoringAdminSite` at `/admin/policy/`, url name
`policy-overview`, following the existing `admin/map/` page: a `get_urls` entry,
`each_context` merged with a context builder, and a template under
`templates/admin/`.

Two ways in:

1. A link on the dashboard, beside the network map.
2. An object-tool button on the Node changelist, via
   `templates/admin/alerts/node/change_list.html` extending
   `django_object_actions/change_list.html`. Extending the
   `django_object_actions` template rather than `admin/change_list.html` is the
   same requirement the existing `change_form.html` documents: skipping it drops
   the action buttons from the page.

The view is gated on the `alerts.view_node` permission rather than plain staff
access. It is read-only, and it shows the same facts the Node page already shows
to a viewer.

## What one row is

One row per node and checker that has any config entry.

| Node | Checker | Policy | Status | Why |
|---|---|---|---|---|
| fiyat-ekrani | cpu | Warning at 99, Critical at 99 | In effect | |
| fiyat-ekrani | memory | Warning at 90 | Saved but not scoring | Set a critical threshold too, or clear both. |
| hub | network | (none) | Not honoured | Nothing reads `network`. |

The three statuses are the three lists `build_effective_policy` already returns:
`sections` is *In effect*, `inactive` is *Saved but not scoring*, `unread` is
*Not honoured*.

A checker can produce both a scoring section and leftover keys nothing reads.
That stays one row: the status is the worse of the two, and the ignored keys are
named in the Why cell. The alternative, a second row for the same checker,
reads as two decisions where an operator made one.

The Why cell carries, in order of precedence: `inactive_reason`, then
`editor_note`, then the list of ignored keys. These are the panel's own
sentences, reused rather than restated, so an operator meets the same wording
wherever the problem appears.

An empty entry such as `{"cpu": {}}` is the marker that opens a section in the
form. It scores nothing and holds nothing to ignore, so it produces no row, the
same way `build_effective_policy` leaves it out of all three lists.

## Editing from a row

Each row gets an Edit button linking to
`/admin/alerts/node/<pk>/change/#id_policy__<checker>__<first field>`.

Those input ids are stable: `node_policy.field_name` builds the form field
names, and Django prefixes them with `id_`. The fragment lands the operator on
that checker's own boxes rather than at the top of a long page. Django's admin
fieldset template carries no per-fieldset id, so the input is the anchor.

A *Not honoured* row has no spec and therefore no boxes. It links to the change
page with no fragment.

## Ordering

Nodes holding any *Saved but not scoring* or *Not honoured* row sort first, then
alphabetically by `instance_id`. Rows inside a node sort by checker name.

Nodes with no config at all get no rows. They are counted in one line at the
bottom: "9 other nodes have no hub-side policy." A table of dashes would bury
the rows that matter.

## Testing

`apps/alerts/_tests/test_policy_overview.py` covers row building: a section in
effect, an inactive section, an unread-only checker, a checker that is both, the
empty-entry marker, the problems-first ordering, the no-policy count, and no
nodes at all.

`apps/alerts/_tests/test_node_admin.py` gains coverage for the view rendering,
the `alerts.view_node` gate, and the changelist button.

100% branch coverage on changed code, per `AGENTS.md`.
