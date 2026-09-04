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

Access is decided by `NodeAdmin.has_view_permission` rather than plain staff
access or a raw `view_node` codename. Django reads that as view OR change, so
anyone who can open a node's own page can open this one. It is read-only, and it
shows the same facts that page already shows.

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
That stays one row, badged with the worse status. *Saved but not scoring* wins
over *Not honoured*, because a half-filled threshold pair is a decision an
operator has to finish while a leftover key changes no severity. The alternative,
a second row for the same checker, reads as two decisions where an operator made
one.

The Why cell joins every sentence that applies: the section's `inactive_reason`,
its `editor_note`, and what the unread keys mean. These are the node page's own
sentences, reused rather than restated, so an operator meets the same wording
wherever the problem appears.

`build_effective_policy` files two different problems under one blank unread key:
no scorer knows the checker at all, or a known checker holds something that is
not a mapping. "Nothing reads cpu" is false in the second case, so the two get
different sentences.

### The cautioned row

A section can score and still hold a value the admin boxes would refuse back.
`{"listening_ports": {"allowlist": [70000]}}` is the case: the scorer coerces
the port, the form's own validation rejects it. The node change page flags that
in amber, so this page does too. The row keeps its green *In effect* badge,
because it really is in effect, and its reason reads in amber instead. It does
not count as a problem for sorting.

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

A checker with no spec has no boxes on that page, so its row links there with
no fragment. Having no spec is not the same as being *Not honoured*: a checker
that does have boxes but holds a malformed entry is *Not honoured* and still
keeps its fragment, because those boxes are exactly where it gets fixed.

## Ordering

Nodes holding any *Saved but not scoring* or *Not honoured* row sort first, then
alphabetically by `instance_id`. Rows inside a node sort by checker name.

Nodes with no config at all get no rows. They are counted in one line below the
groups: "9 other nodes have no hub-side policy." A table of dashes would bury the
rows that matter. That line sits inside the branch that prints the groups, so a
hub where nothing is configured reads "No node on this hub overrides anything"
and nothing else. "Other" needs something to be other than.

## Testing

`apps/alerts/_tests/test_policy_overview.py` covers row building: a section in
effect, an inactive section, an unread-only checker, a checker that is both, the
empty-entry marker, the problems-first ordering, the no-policy count, and no
nodes at all.

`config/_tests/test_policy_overview_view.py` covers the page: the permission
rules, the rendered rows and badges, the escaping, and the quiet-node count.
`apps/alerts/_tests/test_node_admin.py` gains the changelist button, and
`config/_tests/test_dashboard_render.py` the dashboard link.

100% branch coverage on changed code, per `AGENTS.md`.
