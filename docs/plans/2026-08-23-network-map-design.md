---
title: "Network Map: the routing table, drawn"
parent: Plans
---

# Network Map: the routing table, drawn

**Date:** 2026-08-23
**Prior art:** `2026-08-12-routing-simplification-design.md` (§1–2 locked the map's role; §10
sequenced it after routing simplification and push fan-out — both now merged, through PR #212).

## Purpose

Answer "is my routing table sane?" at a glance: every lane, in match order, with its reach and
its delivery state. A configuration-review surface, not a traffic view. The later addition
("where does this alert go?" — a probe input evaluating facts against the table) is explicitly
out of scope here.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Surface | New view on `MonitoringAdminSite`: `GET /admin/map/`, linked from the dashboard | Operators already look here; no new app/auth surface. Supersedes the 2026-08-12 "separate app" lock — that lock's real content was "not inside a pipeline stage", and `config/` satisfies it |
| Builder | `config/netmap.py` → `get_map_context()`, pure projection of `PipelineDefinition` + `NotificationChannel` | Scope discipline: no new models, endpoints, transports |
| Rendering | Server-rendered Django template `templates/admin/map.html`, CSS only, no JS | Matches `dashboard.html`; testable as context data |
| Shadow detection | Symbolic subsumption over the four match ops | Exact for this op set; anything unprovable draws normal (fail toward "not shadowed") |
| Shadowed lanes | Drawn greyed with "shadowed by ‹lane›", never hidden | Hidden looks deleted |
| Refresh | Static snapshot, reload to refresh | Same as the readiness panel |

## What a lane card shows

Cards in priority order (top wins). Each card:

- name, seed-provenance badge (from `tags`), active flag
- `match` rendered as readable conditions (`source is cluster`, `severity in [high, critical]`);
  empty match = "matches everything"
- stage chips in order (`check → analyze → notify`)
- delivery target: channel name + state from `delivery_gap()` — green bound; neutral
  recording-only (no `notify` in stages); red `no_channel` / `no_driver`, gap named

States, distinct: **shadowed** (grey, names the shadowing lane), **inactive** (dimmed/struck —
inactive lanes do not shadow), **never matches** (red — malformed `match` that `matches()`
fails closed on, e.g. unknown op, non-dict condition, non-list `in` value).

## Shadowing: symbolic subsumption

Lane B is shadowed by an earlier **active** lane A when every fact assignment satisfying B's
conditions also satisfies A's — so B can never win first-match. Computed per field over the op
set `is` / `is-not` / `in` / `not-in`:

- A has no condition on a field → A accepts everything there.
- `is v` ⊆ `is v`; `is v` ⊆ `in L` when `v ∈ L`; `in L1` ⊆ `in L2` when `L1 ⊆ L2`;
  `is v` ⊆ `is-not w` when `v ≠ w`; `in L` ⊆ `not-in M` when `L ∩ M = ∅`; etc.
- Any pairing not provable by these rules → not subsumed → B draws normal. `is-not`/`not-in`
  on B's side is generally unprovable against a constraint on A's side (open domains) and is
  left unproven rather than guessed.

The check is O(lanes² × fields) over a table of ~10 rows — computed per request, no caching.

## Edge cases

- Malformed `match` → "never matches" card (same fail-closed semantics as the engine, made visible).
- Empty table → "no routing configured" (the seeded catch-all makes this abnormal).
- Every card links to its `PipelineDefinition` admin change page.

## Testing

All logic in `get_map_context()`; the view/template test smoke-renders only. Subsumption gets a
pairwise case table: equal match, superset-by-conditions, `in`-list containment, `is` vs `in`,
negation overlap (unprovable → not shadowed), malformed, inactive-shadower. 100% branch on
changed code.

## Out of scope

Probe input ("where does this alert go?"), run counts / traffic / last-fired, a `show_routing`
management command, any new storage.
