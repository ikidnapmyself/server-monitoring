---
title: "Base Admin Hardening — Operations Surface"
parent: Plans
---

# Base Admin Hardening — Operations Surface

**Date:** 2026-08-09
**Status:** Design approved, pending implementation plan
**Scope:** Harden the stock Django admin only. No Django Unfold, no new JS/CDN
dependency, no new endpoints. An opt-in admin package layer (e.g. Unfold) is
explicitly deferred to a separate future effort.

## Problem

The cluster pipeline works, but the admin is not a usable operations surface:

- The **inbox** (durable PENDING/PROCESSING pipeline runs) cannot be monitored —
  its state is only visible via the CLI drain.
- **Alert history** is too complex and fragmented; there is no single place to
  trace an incident's full journey.
- **Preflight** results are invisible unless you run the CLI — they are not
  persisted, so nothing survives the terminal session.
- **Pipelines cannot be visualized** by the axes that matter operationally:
  which server (hub vs agent), how the run started (incoming push vs
  checker-generated), and status. Pipeline history reads as undifferentiated
  noise with no proper relationships.
- Trends (e.g. disk filling up) cannot be seen against alert history.

## Non-goals

- No Django Unfold or any third-party admin package in this pass.
- No hub-push of preflight data (kept strictly node-local; a future concern).
- No new HTTP endpoints, auth schemes, or registries — admin-only changes that
  reuse existing mechanisms (`process_inbox`, the Node registry, `CheckRun`).

## Key modeling decision — two orthogonal axes

"Node" was conflating two different questions. They are separated here:

| Axis | Question | Agent alert | Hub self-check |
|---|---|---|---|
| **Subject (node)** | *Which server is this about?* | the agent Node | the hub itself |
| **Origin** | *How did this pipeline start?* | `incoming_webhook` | `checker_generated` |

Server-checkers vs. node-alerts is a difference on the **origin** axis, not a
conflict on the **node** axis. Because agents run their own checkers and push
results to the hub, on the hub those arrive as `incoming_webhook`; the only
`checker_generated` pipelines on the hub are **the hub monitoring itself**.

To keep the node axis uniform, the **hub is materialized as a self-node**. The
project is already a peer-node model and the hub already has an identity
(`settings.INSTANCE_ID`); this simply gives it a `Node` row like any peer.

## Components

### 1. Hub self-node — `apps/alerts`

- Add `is_self` boolean to `Node` (default `False`).
- `Node.ensure_self()` classmethod + `bootstrap_self_node` management command
  that upserts a `Node` from `settings.INSTANCE_ID` (hostname auto-filled).
  Idempotent; also invoked lazily when a `checker_generated` pipeline is created.
- Admin: `is_self` badge, hostname, `last_seen`. The Node page becomes the hub
  for "this server."

### 2. Pipeline node + origin — `apps/orchestration`

- Add to `PipelineRun`:
  - `node` FK → `alerts.Node`, `null=True`, `on_delete=SET_NULL`.
  - `origin` enum: `incoming_webhook` / `checker_generated` / `manual`.
- Populate at creation:
  - incoming webhook → node resolved from instance_id/incident,
    `origin=incoming_webhook`;
  - `--checks-only` → node = self-node, `origin=checker_generated`;
  - CLI / manual → `origin=manual`.
- **Backfill migration:** infer `origin` from `source`; copy `node` from
  `incident.node` where an incident is present.
- Admin: `list_filter` on node, origin, status, current_stage; clear
  columns — this delivers the "group by hub/agent + incoming vs
  checker-generated + status" requirement.

### 3. Inbox monitor — `apps/orchestration`

- `InboxItem` **proxy model** over `PipelineRun`, registered in admin, filtered
  to PENDING/PROCESSING, oldest-first.
- Columns: run_id, source, node, origin, status, **age**, **stuck?**
  (PROCESSING past the reclaim cutoff).
- Admin actions: **Drain selected now** and **Reclaim stuck**, reusing
  `process_inbox` internals (no logic duplication).

### 4. Preflight persistence — `apps/checkers`

- New node-local models:
  - `PreflightRun`: created_at, node/instance_id, passed/warn/error counts,
    overall_status, triggered_by.
  - `PreflightCheck`: FK → PreflightRun, name, level, message, hint.
- The `preflight` command **persists a `PreflightRun` + child checks on every
  run by default**, with a `--no-save` opt-out (CI / ad-hoc). A simple
  retention/pruning note is included to bound DB growth. **No hub-push.**
- Admin: history list with pass/warn/error counts and overall status,
  `date_hierarchy`, readonly inline of checks.

### 5. Incident timeline (alert-history cleanup) — `apps/alerts`

- On the `Incident` admin page, a **readonly merged timeline** combining
  `AlertHistory` events + `StageExecution`s + notification refs in chronological
  order, with `trace_id` / `run_id` shown once. This realises the pipeline
  contract: from a notification, jump back to the full journey.
- Simplify `AlertHistoryAdmin` defaults: human event labels, collapse raw
  `details` JSON, better filters. History remains readonly (audit records).

### 6. Inline SVG sparklines — shared admin util + `apps/checkers`

- `render_sparkline(points, markers=…) -> SafeString`: self-contained inline
  `<svg>`, no JS, no external assets (honours the self-contained/CSP rule).
- Rendered on Node and Incident pages: disk % (and other `CheckRun` metrics)
  over time, with alert-firing markers overlaid. Data sourced from `CheckRun`
  metric history for that node/metric.

### 7. Navigation / "missing models"

- Finding: every model **is** already registered — the real gap is
  relationships/navigation. Addressed by the above: the Node page gains inlines
  for recent pipelines, recent alerts, latest preflight, and the disk
  sparkline; plus cross-links between `PipelineRun` ↔ `Node` ↔ `Incident`.

## Data flow

```
webhook ingest  → PipelineRun(node=<agent>, origin=incoming_webhook, PENDING)
                    → InboxItem view shows it until process_inbox drains it
checks-only run → PipelineRun(node=<self-node>, origin=checker_generated)
preflight run   → PreflightRun (+ PreflightCheck children), node-local
CheckRun metrics ─┐
AlertHistory ─────┼→ Incident page: merged timeline + disk sparkline
StageExecution ───┘
```

## Error handling & edge cases

- Self-node bootstrap must be idempotent and safe when `INSTANCE_ID` is unset
  (skip/no-op with a clear message rather than creating a blank node).
- `node` FK is nullable and `SET_NULL` so node deletion never orphans/deletes
  pipeline history.
- Backfill migration must be reversible and tolerate rows with no incident and
  ambiguous `source` (fall back to a sensible origin default).
- Inbox "Drain now" / "Reclaim stuck" must reuse the atomic claim in
  `process_inbox` so admin-triggered drains never double-process.
- Sparkline renderer must handle empty/short series and never emit external
  references.
- Preflight persistence must not break `--json` / CI usage; `--no-save`
  preserves the old print-only behaviour.

## Testing

- 100% branch coverage on all changed code (`uv run coverage run -m pytest &&
  uv run coverage report`).
- Self-node: bootstrap idempotency, missing `INSTANCE_ID`, lazy creation.
- PipelineRun node/origin: creation paths (webhook / checks-only / CLI) and the
  backfill migration (with/without incident, ambiguous source).
- InboxItem: filtering, age/stuck computation, drain + reclaim actions.
- Preflight models + command: default persist, `--no-save`, `--json`,
  counts/overall_status correctness.
- Incident timeline: chronological merge across the three sources, readonly.
- Sparkline: empty series, single point, marker overlay, no external refs.

## Acceptance criteria

- Admin can monitor the inbox (pending/processing/stuck) and drain/reclaim from
  the UI.
- Pipelines are filterable/groupable by node (hub self-node + agents), origin,
  and status.
- Preflight results are persisted by default and browsable with history.
- Incident page shows a single readable merged timeline.
- Node/Incident pages show disk-usage sparklines with alert markers (inline SVG,
  no JS/CDN).
- No Unfold, no new endpoints; base admin only; CI green; coverage 100% on
  changed lines.
