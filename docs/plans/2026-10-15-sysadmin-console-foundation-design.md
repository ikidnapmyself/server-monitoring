---
title: "Sysadmin Console Foundation"
parent: Plans
---

# Sysadmin console foundation

## Problem

The console at `/console/` looks like GitHub after the Primer restyle, but what it shows is
still database rows. Rendered against a real database, an operator reads:

| Shown | Comes from |
|---|---|
| `NETWORK Check Alert`, `DISK_MACOS Check Alert` | the alert name the checker bridge builds |
| `checked · 345043fd-95ff-…` | run status enum and run UUID |
| `— → firing` | a raw `AlertHistory` transition |
| `ok cpu_temp Skipped: not Linux` | a registry key, and a skip reported as ok |
| `17544.1 MB recoverable` | a hand-formatted checker message |
| `3 lane(s) cannot deliver: catch-all (no_channel)` | reason codes as the only explanation |
| `Error code: 401 - {'type': 'error', …}` | a raw provider exception |
| `80% confidence` on that failed analysis | the intelligence fallback, which is wrong |
| Raw JSON, "Open in admin" and "Edit raw" on every entry | debugging affordances in the main view |

This is not a styling problem. Nothing upstream is accountable for meaning, so the console
has nothing human to render. The worst case is a display string doubling as a key:
incident grouping matches on `alert.name` (`apps/alerts/services.py`
`_find_open_incident`), and `Incident.title` is a copy of it. `NETWORK Check Alert` is
ugly on purpose, because a human name carrying live text would split incidents.

## Goal

The console is a tool for sysadmins. Every screen answers four questions without
decoding: **what's wrong, where, how bad, and what do I do about it.** Each problem
carries its fix as an action. Identifiers and raw output are one click away, never in the
main view.

There is no user base, so data can be reset and fundamentals changed.

## Decisions

- Each app describes its own output. The DB stores machine keys and structured data. Human
  text is rendered on read by the owning app, and the console and notifications both use
  it. Writing text at produce time was rejected because it freezes wording into rows and
  trusts node-written text. A console-only translation layer was rejected because nobody
  upstream would answer for meaning and notifications would stay raw.
- The console replaces the Django admin as the operator surface. `/admin/` ends as stock
  Django, superuser-only, kept for break-glass and account management. This reverses the
  2026-10-11 design's decision to keep the admin as the config editor.
- Console access equals Django admin access: active staff, plus the model permissions
  Django already assigns. The console builds no user, role or group management.

## 1. Identity and grouping

- Alert identity stays `(fingerprint, source)`, with `check:{instance_id}:{checker}` for
  checker alerts.
- New `Alert.subject`: a machine key for the kind of problem. Checker alerts write
  `checker:<name>`. Webhook drivers write `<source>:<alertname>`.
- `Incident` gains `subject`, `instance` and `node`. Grouping is an open or acknowledged
  incident with the same `(subject, instance)`, in one indexed query. `instance` comes from
  the alert's labels, so a webhook alert with no registered node still groups. Two hosts
  with the same problem stay two incidents.
- `Alert.name` stops being load-bearing. Webhook alerts keep the name their source gave,
  which is already human. Checker alerts stop writing `<CHECKER> Check Alert`. The "alert
  name is deliberately stable" rule leaves both AGENTS.md files.
- `Incident.title` and `Incident.description` are dropped as stored columns. Today they
  are copied from the first alert and never updated, which is two of the three symptoms in
  `2026-09-14-refire-staleness-open-question.md`. The headline and detail are rendered
  from the subject, node and current alerts. The first-fire timestamp symptom is the
  episode question and stays out of scope.
- `CheckStatus.SKIPPED` is added. A skip never raises an alert.
- No reset. `subject`, `instance` and `node` derive from data already on every row, so the
  migration that adds them backfills them. The two columns are dropped in PR 2, once
  `describe_incident` replaces them.

## 2. The describer contract

`config/describing.py` is a shared utility, not an app:

```python
Description(
    headline: str,
    detail: str = "",
    tone: str,                 # a tone name from config/admin_badges.py
    action: Action | None,
    facts: list[Fact] = [],    # technical facts for the Details disclosure
)
Action(text: str, intent: str = "", params: dict = {})
Fact(label: str, value: str, copy: bool = False)
```

Describers return an intent, never a URL, so nothing upstream imports `apps.console`.
The module also holds the formatters: bytes, durations, percentages and host lists.

| App | Describes |
|---|---|
| checkers | `BaseChecker` gains `label`, `problem` and `describe(status, metrics)`. The default falls back to `message`, and each of the 15 checkers overrides it. |
| alerts | `describe_incident` reads `{problem} on {host}`, with detail and action from the worst current alert. A webhook alert uses its own name and description, and a `runbook_url` annotation becomes its action. |
| orchestration | Runs and refusal reasons, through a registry from reason code to sentence and action. |
| intelligence | Analysis outcomes. Failures are classified as auth, rate limit, timeout or provider down. A failed analysis reports no confidence and no recommendations. |
| notify | Delivery outcome per channel, failures classified the same way. |

Node traffic is described on the hub from stored metrics, using the hub's own checker
class. A checker the hub doesn't know falls back to the node's message with a "described
by the node" fact.

Guard tests: every registered checker defines `label` and `problem`. Every reason code
has a description. No headline matches a UUID or `snake_case` pattern.

## 3. Console rendering

- `console/partials/_description.html` renders every `Description`: headline, detail,
  action, and a collapsed Details disclosure with the facts.
- `apps/console/intents.py` maps each intent to a route, a method and a required
  permission. Links cover config fixes such as `edit_channel`, `edit_route` and
  `edit_provider`. POST buttons cover `rerun_check`, `acknowledge` and `retry_run`, each
  through an action view that calls the owning app. A user without the permission, or an
  unknown intent, gets the action as text. A completeness test fails on an intent the
  table doesn't know.
- Details holds IDs with copy buttons, keys, fingerprints and metrics as key/value rows,
  and raw JSON last, collapsed. The console never links to `/admin/`.
- The incident page leads with a "What's wrong / Do this" box. Check results list
  problems first and fold OK and skipped results into counts. Overview readiness rows are
  descriptions with fixes, one line when all green. Times are relative with the absolute
  time on hover.
- Guard test: every page renders against a fixture covering each stage outcome and reason
  code, and fails if visible text outside Details holds a UUID, a `snake_case` token, a
  reason code or a `{`.

## 4. Notifications

The orchestrator calls the describers and passes the results to notify, which stays dumb:

```python
render_ctx = {
    "incident": Description,
    "findings": [Description],      # failing checks, worst first
    "analysis": Description | None,
    "severity": str,
    "console_url": str,             # empty when CONSOLE_URL is unset
}
```

All four templates rebuild on these keys, and the raw `check`, `intelligence` and
`ingest` keys go. Messages carry the trace ID in the footer and no other facts. The new
`CONSOLE_URL` setting adds an incident link when set. `HUB_URL` is not reused because on
an agent it points at another machine.

## 5. Delivery

Fundamentals first:

1. Identity keys: `subject`, incident `subject`, `instance` and `node`, grouping, and
   `CheckStatus.SKIPPED`. Plan: `2026-10-15-sysadmin-console-pr1-identity.md`.
2. Describer contract, formatters, all checker describers, incident and alert describers.
   Drops `Incident.title` and `Incident.description`.
3. Reason registries for orchestration, intelligence and notify. Removes the fake
   confidence.
4. Notify on describers.
5. Console on describers: Overview, Incidents and Incident retrofitted, intents, Details,
   guard test. Console reads switch to the admin's `view_*` checks.

Domain slices. Each ships console pages, actions and forms, lifts logic out of its
`ModelAdmin`, and cuts that `ModelAdmin` to stock in the same PR:

6. Alerts: list, detail, re-evaluate. Bulk resolve goes through `IncidentManager` and
   writes history.
7. Runs and inbox: list, detail, retry, mark failed, drain, reclaim.
8. Nodes and policy: grid, node page, policy editing, re-evaluate open alerts with a
   double-submit guard.
9. System: routes, channels, providers, API keys, network map, readiness. Channel forms
   are typed per driver from fields each driver declares.
10. Reports and exports.
11. Break-glass: delete the admin dashboard, `SECTION_MAP`, the skin, the admin templates
    and custom `ModelAdmin` code. Register models with plain `ModelAdmin`,
    superuser-only. `/` and the login page move to the console.

The console branch `feat/console-primer` merges before PR 1. Each PR updates the
AGENTS.md files it invalidates.

## Out of scope

- The refire episode decision.
- HMAC and node-bound keys.
- User, role and group management in the console.

## Acceptance

- A sysadmin can tell what's wrong, where, how bad and what to do from every page without
  opening Details.
- No console page or notification shows a UUID, a `snake_case` token, a reason code or raw
  JSON outside Details.
- Nothing groups or matches on display text.
- After PR 11, `/admin/` is stock Django, superuser-only, and no operator task needs it.
