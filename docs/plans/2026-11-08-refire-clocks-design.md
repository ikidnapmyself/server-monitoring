---
title: "Refire Clocks"
parent: Plans
---

# A refired incident reads from its latest fire

Answers the 2026-09-14 open question on what a refire should do to an alert and its
incident: one row refreshed, a row with episodes, or a new row per episode. The user chose
the first on 2026-11-08.

Since that question was raised, incidents stopped storing a title or description
(convention 14). The incident page describes the current lead alert on read, and a refire
already updates the alert's name and description. What stays stale is every clock: an
incident that resolved three weeks ago and fired again five minutes ago reads "Started 3
weeks ago, 3 weeks so far".

## Design

**Model.** `Incident.opened_at` is when the current episode began. `Incident` sets it on
create, and `reopen()` sets it to now. A migration adds the field and backfills it from
`created_at`. `created_at` keeps meaning the first fire.

**Incident page** (`apps/console/queries/incident.py`, `_incident.html`):

- When `opened_at` is later than `created_at`, the header shows "Fired again <opened_at>"
  next to "Started <created_at>".
- Duration runs from `opened_at` to `resolved_at` or `closed_at`, or to now.
- The timeline gains an "Incident fired again" entry at `opened_at` when the incident has
  reopened. Earlier resolutions stay lost, the accepted cost of not modelling episodes.

**Incident list and overview** (`queries/incidents.py`, `queries/overview.py`): sort, the
time-window filter and the row clock use `opened_at`. A reopened row reads "fired again
<time>" in place of "opened <time>". The overview's incidents-per-day count stays on
`created_at`, because it counts first fires.

**Alert page** (`queries/alert.py`, `_alert.html`): the header shows "Fired again <time>"
from the alert's newest `refired` `AlertHistory` row, when one exists. No new alert field.
`started_at` keeps its origin meaning.

**Every reopen counts.** A refire under a resolved incident, a firing alert under a closed
one, and a re-evaluation that reopens all go through `Incident.reopen()`, so all set
`opened_at`.

**Out of scope.** Notification text is described on read and is already current. Per-episode
history (shape 2) and a row per episode (shape 3) are not built.

## Tests

- `reopen()` sets `opened_at`. Create sets it. The migration backfills `created_at`.
- A refire through `AlertOrchestrator` under a resolved incident moves `opened_at`.
- The incident page shows "Fired again" and a duration from `opened_at` only for a reopened
  incident, and the timeline entry appears only then.
- The list sorts and windows on `opened_at`, and a reopened row reads "fired again".
- The overview per-day count still counts by `created_at`.
- The alert page shows "Fired again" from the newest `refired` history row, and nothing when
  there is none.
