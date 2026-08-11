---
title: "Remove the Hub Self-Node Concept (YAGNI Cleanup)"
parent: Plans
---

# Remove the Hub Self-Node Concept (YAGNI Cleanup)

**Date:** 2026-08-11
**Status:** Design approved, pending implementation plan
**Scope:** Delete the hub "self-node" concept introduced in the admin-hardening
work (PR #202). It is inert in production, read by no business logic, and not
required for alert management.

## Why remove it

The self-node was meant to materialize the hub as a first-class `Node` so the
node axis would be "uniform." In practice:

- **It never works in production.** `Node.ensure_self()` no-ops when
  `settings.INSTANCE_ID` is empty, and `INSTANCE_ID` is an *agent*-side setting
  the hub does not set. So the hub self-node is never created.
- **Nothing consumes it.** `is_self` is read by no business logic — only shown
  in the Node admin list/filter. `ensure_self()` has one functional caller:
  `orchestrator._resolve_node` stamps `PipelineRun.node` for the hub's
  `checker_generated` runs.
- **Alert management does not need it.** Incident/alert grouping already works
  via `incident_instance_key` (instance_id → instance → hostname). Agent alerts
  group by `instance_id` (a registered `Node`); hub self-check alerts group by
  `hostname`. Both are visible on the alerts page and in the dashboard incident
  totals today — confirmed as the desired behavior.

Making it work would require inventing a stable hub identity (a new setting or a
hostname fallback) to serve a convenience nobody consumes. Not worth it.

### Considered and rejected: a per-source dashboard view

We considered building a dashboard panel that groups incidents/alerts by source
node (which *would* give the hub a real reason to have an identity). Decision:
**not now.** Hub alerts already appear in the dashboard incident totals and on
the alerts page; a per-source breakdown is a separate, larger feature that is not
currently wanted. This design is a pure deletion.

## Removed

- `Node.is_self` field (`apps/alerts/models.py`) + a `RemoveField` migration.
- `Node.ensure_self()` classmethod (and the now-unused `socket` import if it
  becomes unused).
- `bootstrap_self_node` management command
  (`apps/alerts/management/commands/bootstrap_self_node.py`) and its test.
- `is_self` from `NodeAdmin.list_display` / `list_filter` (`apps/alerts/admin.py`).
- The `is_self` / `ensure_self` tests in `apps/alerts/_tests/test_models.py` and
  `test_admin.py`.
- The `if origin == CHECKER_GENERATED: return Node.ensure_self()` branch in
  `orchestrator._resolve_node` — all origins now flow through the single
  label-based resolution path.
- Self-node wording in `apps/alerts/AGENTS.md` and the two `help_text` strings
  that mention "hub self-node".

## Kept (deliberately — broader than the self-node)

- `PipelineRun.node` FK and the `PipelineOrigin` enum. `origin`
  (`incoming_webhook` / `checker_generated` / `manual`) still usefully classifies
  runs; `checker_generated` stays a valid label, just no longer node-mapped.
- The dashboard nodes-readiness card (uses `Node.count()` / `last_seen`, never
  `is_self`).
- `docs/plans/*` design docs (historical record) — left unchanged, including the
  admin-hardening design that introduced the self-node.

## Behavior change

The hub's own `--checks-only` pipelines, which *would* have received
`node=ensure_self()`, now get `node=null` — via the single label-resolution path
(no `instance_id` in a self-check payload). In production this is a **no-op**:
`ensure_self()` already returns `None` there, so those runs are already
`node=null`. Their alerts continue to group by `hostname` exactly as today.

## Testing

- Delete the obsolete tests (`is_self`, `ensure_self`, `bootstrap_self_node`).
- Flip `test_checker_generated_run_gets_self_node` → assert `run.node is None`
  (rename accordingly).
- Full suite green; 100% branch coverage on changed lines.
- `makemigrations --check --dry-run` clean after the `RemoveField` migration.
- `bandit` / `black` / `ruff` clean.

## Acceptance criteria

- No references to `is_self`, `ensure_self`, or `bootstrap_self_node` remain in
  `apps/`, `config/`, or `bin/` (design docs excepted).
- `Node` has no `is_self` column (migration applied); `_resolve_node` has a
  single resolution path.
- Full CI green; coverage 100% on changed lines; no behavior change to deployed
  alert grouping.
