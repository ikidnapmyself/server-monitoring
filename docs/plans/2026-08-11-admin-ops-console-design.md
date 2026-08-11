---
title: "Admin Ops-Console — Readiness Panel + Task-Oriented Navigation"
parent: Plans
---

# Admin Ops-Console — Readiness Panel + Task-Oriented Navigation

**Date:** 2026-08-11
**Status:** Design approved, pending implementation plan
**Scope:** Make the Django admin friendlier as an operations console — extend the
existing `MonitoringAdminSite` dashboard with a configuration-**readiness** panel,
and regroup the scattered models into operator-facing sections. No new admin
package/dependency; builds on what already exists.

## Problem

The admin already has a custom dashboard (`config/admin.py` →
`MonitoringAdminSite`, `config/dashboard.py`, `templates/admin/dashboard.html`)
showing **runtime activity**: active incidents, 24h pipeline health, recent check
runs, failed pipelines, 7-day trends, LLM token usage.

Two gaps make it feel confusing / "loosely coupled":

1. **No configuration readiness.** You cannot tell at a glance whether the system
   is actually wired up to do its job — are notification channels active (else
   alerts go nowhere)? is an LLM provider live (else analysis falls back to "no
   AI")? did the last preflight pass? is the inbox draining? These live in
   separate admin sections and must be hunted down.
2. **Model navigation is Django-app-shaped, not operator-shaped.** The 13 models
   are grouped by the five Django apps (Alerts / Checkers / Intelligence / Notify
   / Orchestration), which doesn't match how an operator thinks about the system.

## Non-goals

- No Django Unfold or other third-party admin UI.
- No new endpoints or models.
- The **self-node creation condition** (`ensure_self()` no-ops when `INSTANCE_ID`
  is unset, so the hub self-node never appears in production) is a **separate,
  later step** — explicitly out of scope here. The readiness "Nodes" card still
  works for agent nodes regardless.

## Component 1 — Readiness panel (dashboard)

Extend `get_dashboard_context()` with a `readiness` block (a list of
`{label, status, detail, url}` entries), rendered as status cards
(green / amber / red + a deep link) **above** the existing activity cards.

| Signal | Source | Green / Amber / Red |
|---|---|---|
| **Notification channels** | `NotificationChannel.is_active` | ≥1 active / some inactive but ≥1 active / **none active → alerts go nowhere** |
| **LLM provider** | `IntelligenceProvider.is_active` (one active max) | 1 active / — / **none active → analysis falls back to "no AI"** |
| **Preflight** | latest `PreflightRun.overall_status` | ok / warn / error (neutral when never run) |
| **Inbox** | `InboxItem` (PENDING/PROCESSING) + stuck | empty / backlog present / **stuck runs present** |
| **Nodes** | `Node` last-seen recency | seen recently / stale / none seen |

- `status` is one of `ok` / `warn` / `error` / `neutral`, mapped to a CSS class.
- Each card links to the relevant changelist (channels, providers, preflight
  history, inbox, nodes).
- Pure read-aggregation (counts + one "latest" per signal), same pattern as the
  existing context builder. No writes, cheap queries.

## Component 2 — Task-oriented navigation

Override `MonitoringAdminSite.get_app_list()` to re-bucket **all registered
models** into three operator-facing sections, replacing the default per-app
grouping on both the index and the sidebar. It reuses Django's already
permission-filtered model dicts (from `super().get_app_list()` /
`_build_app_dict`), so no model a user lacks permission for is exposed.

| Section | Models (explicit order, most-used first) |
|---|---|
| **Operations** (daily triage) | Incident, Alert, Inbox, Pipeline Runs, **Nodes** |
| **Configuration** (wiring) | Notification Channels, LLM Providers, Pipeline Definitions, API Keys |
| **History & Audit** (records) | Check Runs, Preflight Runs, Analysis Runs, Alert History, Stage Executions |

- A section with zero visible models (after permission filtering) is hidden.
- A model not listed in any section falls back to a trailing "Other" section (so
  a future model is never silently dropped from the nav).
- The readiness cards deep-link into the Configuration/Operations sections.

## Component 3 — Rendering, styling

- Readiness cards added to `templates/admin/dashboard.html` above the current
  activity cards. A small helper in `config/dashboard.py` builds the readiness
  entries; all dynamic content escaped via `format_html` (consistent with the
  existing `prettify_json`).
- Theme-aware using the same `var(--body-bg / --body-fg / --hairline-color)` CSS
  variables already used on the dashboard — no external assets, no JS.
- Status → color via a small class map (`ok`/`warn`/`error`/`neutral`).

## Error handling & edge cases

- Empty models (no channels / no provider / no preflight yet / no nodes) render
  as the specified neutral-or-red state; never raise.
- `get_app_list` tolerates unregistered/missing models and empty sections; the
  "Other" fallback prevents silent model loss.
- Readiness queries are bounded aggregates; no N+1 on the index page.

## Testing

- Unit tests per readiness signal across permutations: channels
  (none/active/mixed), provider (none/active), preflight
  (none/ok/warn/error), inbox (empty/backlog/stuck), nodes
  (none/recent/stale).
- `get_app_list` tests: correct bucketing + order, permission-filtered models
  excluded, empty section hidden, unlisted model lands in "Other".
- Dashboard render test: readiness cards present with the correct status class
  and deep links.
- 100% branch coverage on changed code (repo convention).

## Acceptance criteria

- The dashboard shows a readiness panel with the five signals, correct
  green/amber/red status, and working deep links.
- The admin index + sidebar group models into Operations / Configuration /
  History & Audit (Nodes under Operations), permission-safe, empty sections
  hidden.
- No new dependency/endpoint/model; theme-aware, self-contained; CI green;
  100% branch coverage on changed lines.
