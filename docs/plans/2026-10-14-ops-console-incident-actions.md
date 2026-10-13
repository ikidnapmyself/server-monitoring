---
title: "Ops Console Incident Actions Implementation Plan"
parent: Plans
---

{% raw %}

# Ops console incident actions implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task by task.

**Goal:** Ship PR 3 of the console rebuild: acknowledge, resolve and close an incident
from its console page.

**Architecture:** The status rules that live inside `IncidentAdmin` methods move into
`apps/alerts`. The admin and the console both call the lifted function. The console
adds one POST-only view. No new write path.

**Tech stack:** Django 5.2, the vendored htmx 2.0.11, pytest-django.

Design: `2026-10-11-ops-console-rebuild-design.md`. App rules: `apps/console/AGENTS.md`
and `apps/alerts/AGENTS.md`.

## Scope change from the design

The design put every operator action in PR 3. Run retry, mark-failed and re-evaluate
act on runs, alerts and nodes, and the console has no page for those yet. Each moves to
the PR that adds its page: runs and alerts in PR 4, nodes in PR 5. PR 3 covers the
incident transitions only.

## Ground rules

- Branch `feat/console-actions`, cut from `feat/console-incidents`. Never commit to
  `main`. No worktrees.
- A commit runs the full suite and mypy through pre-commit. Never pass `--no-verify`.
- TDD. Absolute imports. Line length 100. No comments that restate the code.
- A transition enqueues a `MANUAL` pipeline run that can send real notifications. Tests
  must never drain the inbox.
- 100% branch coverage on changed code.

---

### Task 1: Lift the transition rules into `apps/alerts`

**Files:** modify `apps/alerts/services.py`, `apps/alerts/admin.py`,
`apps/alerts/AGENTS.md`. Tests in `apps/alerts/_tests/`.

Today the rules sit in three `IncidentAdmin` methods and `IncidentManager` applies
whatever it is told. Two submits of the same button each enqueue a run.

Add to `apps/alerts/services.py`:

- `INCIDENT_ACTIONS = ("acknowledge", "resolve", "close")`.
- `available_incident_actions(status: str) -> tuple[str, ...]`. Acknowledge only from
  open. Resolve from anything but resolved and closed. Close from anything but closed.
  These are the admin's current rules, unchanged.
- `TransitionOutcome`, a frozen dataclass: `ok: bool`, `message: str`,
  `incident: Incident`.
- `IncidentManager.transition(incident_id, action, *, actor="", summary="") ->
  TransitionOutcome`. Inside one `transaction.atomic()` it loads the incident with
  `select_for_update()`, checks the action against `available_incident_actions`, and
  either delegates to the existing `acknowledge`, `resolve` or `close`, or returns
  `ok=False` without writing or announcing. An unknown action raises `ValueError`. A
  missing incident raises `Incident.DoesNotExist`.
- Messages keep the admin's wording: `Incident '<title>' acknowledged.`,
  `Cannot acknowledge — status is '<status>'.`, `Already <status>.`, `Already closed.`

The existing `acknowledge`, `resolve` and `close` already open their own atomic block,
which nests as a savepoint. They must reuse the locked row's state, so the check and
the write can't straddle another writer.

`IncidentAdmin.acknowledge_incident`, `resolve_incident` and `close_incident` become
thin callers of `transition` that pass the outcome to `message_user`, with level
`warning` when `ok` is false. The bulk actions `acknowledge_selected` and
`resolve_selected` call `transition` per incident and count the `ok` outcomes.

Tests: every status against every action, a refused transition writes nothing and
enqueues nothing, an accepted one enqueues exactly one run, a second identical call is
refused and enqueues none, unknown action, missing incident, and the admin's existing
action tests still pass with their messages unchanged.

Commit: `refactor(alerts): lift incident transition rules out of the admin`.

### Task 2: Console action view

**Files:** create `apps/console/views/incident_action.py`. Modify
`apps/console/urls.py`, `apps/console/views/__init__.py`, `apps/console/views/base.py`
if a shared POST guard helps, `apps/console/queries/incident.py`,
`templates/console/partials/_incident.html`, `static/console/console.css`,
`apps/console/AGENTS.md`. Tests in `apps/console/_tests/`.

**View.** `IncidentActionView(ConsoleView)` at `incidents/<int:pk>/<str:action>/`,
name `console:incident-action`.

- POST only. A GET returns 405.
- Staff refusal comes first, as on every console view. Then
  `request.user.has_perm("alerts.change_incident")`, else 403. That is the permission
  the admin's change actions need.
- An action outside `INCIDENT_ACTIONS` is a 404. A missing incident is the console 404
  page.
- Calls `IncidentManager.transition(pk, action, actor=request.user.get_username(),
  summary=...)`. `summary` is read only for resolve, stripped, capped at 2000
  characters.
- The outcome becomes a Django message, `success` or `warning`.
- An htmx request gets the incident partial re-rendered with status 200, whatever the
  outcome. A plain POST gets a 303 redirect to the incident page. The redirect keeps
  `?all=1` only if the form posted it as a hidden field.

**Read model.** `IncidentDetail` gains `actions: tuple[IncidentAction, ...]`.
`IncidentAction` has `key`, `label` and `url`. `build_incident_detail` takes
`can_act: bool = False` and fills `actions` from `available_incident_actions` only when
it is true. The view passes `request.user.has_perm("alerts.change_incident")`.

**Template.** In the header, one `<form method="post">` per available action, with
`{% csrf_token %}`, `hx-post` to the action URL, `hx-target="#incident"`,
`hx-swap="innerHTML"`, `hx-sync="#incident:replace"` and
`hx-disabled-elt="find button"`. Button ids `action-<key>`. Resolve also offers a
`<details id="resolve-note">` holding a second form with a `summary` text field and its
own submit, because an open `<details>` pauses polling and a polled swap would
otherwise wipe what the operator is typing. Messages render at the top of the partial
through the messages framework, tone by level, never `|safe`.

A user without the permission sees no buttons and no empty container.

**Tests.** Anonymous redirect, non-staff 403, staff without the permission 403, GET
405, unknown action 404, missing incident 404, each action through the view with the
run enqueued, a stale action re-rendering with a warning and no run, htmx versus plain
POST responses, CSRF enforced with `Client(enforce_csrf_checks=True)`, buttons shown
per status and hidden without the permission, a hostile summary stored and rendered
escaped, the `FORBIDDEN` scan over the rendered page with buttons and with a message.

Commit: `feat(console): acknowledge, resolve and close from the incident page`.

### Task 3: Verify and open the PR

Run `black --check`, `ruff`, `mypy`, `bandit`, `pip-audit --strict` and the full suite
under coverage. Open the PR against `feat/console-incidents`, or `main` if that branch
has merged.

{% endraw %}
