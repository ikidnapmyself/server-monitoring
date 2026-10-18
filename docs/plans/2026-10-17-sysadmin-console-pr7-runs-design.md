---
title: "Sysadmin Console PR 7: Runs and Inbox Design"
parent: Plans
---

# Sysadmin console PR 7: runs and inbox

Domain slice 7 of `2026-10-15-sysadmin-console-foundation-design.md`. It moves pipeline runs
and the inbox to the console, and cuts `PipelineRunAdmin`, `StageExecutionAdmin`, `InboxAdmin`
and `IncidentAdmin` to stock.

## The queue is the table

There's no broker. A `PipelineRun` row with status `PENDING` is a queued job.
`process_inbox`, run by `server-monitoring-inbox.service`, claims it `PENDING → PROCESSING`
with one conditional `UPDATE` (`inbox.claim`) and calls `execute_run`. Every action below is a
status change on that row or a call into `apps/orchestration/inbox.py`.

## Decision: stuck has one definition

A drain that crashes, or is killed mid-run by gunicorn's 120 second timeout on an in-request
drain, leaves its run in flight with `completed_at` unset. Its status is whatever stage it
reached, so a run can sit at `CHECKED`, `ANALYZED` or `INGESTED` forever, not only at
`PROCESSING`.

`inbox.stuck()` is the one definition, a `Q`: status `PROCESSING`, `INGESTED`, `CHECKED`,
`ANALYZED` or `RETRYING`, `completed_at` null, and `updated_at` older than
`DEFAULT_STALE_MINUTES`. Every stage save stamps `updated_at`, so a live drain keeps its run
fresh, and `completed_at` tells a lane that ended at diagnose from one killed there.
`reclaim_stuck`, `abandon`, the run page, the inbox page and the dashboard all read it.
`InboxItem.is_stuck` goes, since nothing called it.

`reclaim_stuck` returns every stuck run to `PENDING` with `updated_at` stamped. The next
drain resumes it and skips the stages that already succeeded.

## Decision: retry re-queues the run

The admin's "Mark for retry" set a failed run to `RETRYING` and stopped. Nothing picks up a
`RETRYING` run, so the button did nothing.

Retry now puts the run back in the queue: `inbox.requeue(pk)` runs
`filter(pk=pk, status=FAILED).update(status=PENDING, total_attempts=F("total_attempts") + 1)`
and returns whether it won. The next drain claims the run and calls `execute_run`, which shares
`_execute_pipeline` with `resume_pipeline`. That skips every stage with a `SUCCEEDED`
execution and reloads its output, so a run that failed at notify re-runs only notify, on its
stored `{"downstream_incident_id": N}` payload. The run keeps its `run_id` and `trace_id`.

Nothing executes in the request. On a box with no inbox service, the operator drains the run
from the inbox page. `RETRYING` keeps its meaning: the status a resume sets while it runs.

The conditional update makes retry idempotent. A double submit or a race flips the row once,
and the loser gets "This run is no longer failed."

## Pages

### `/console/runs/`

A list of runs, newest first. Each row shows the `describe_run` headline, the incident it
served, the node, origin, the stage it reached, attempts and age.

Filter chips: status (failed, queued and in progress, finished, all), origin, node and since.
The status chips split runs the way `describe_run` words them. A lane without notify ends at
diagnose or analyze, so a `CHECKED`, `ANALYZED` or `INGESTED` run is finished only when
`completed_at` is set, and queued and in progress otherwise.
`q` matches run ID, trace ID and alert fingerprint, the admin's search. `RunFilter` lives in
`filters.py` on the same helpers as `AlertFilter`.

### `/console/runs/<pk>/`

- The `describe_run` headline, tone and fix action.
- The incident as a card linking to the console incident page, and the node.
- One timeline entry per `StageExecution`, every attempt, through `describe_stage_outcome`.
  Each entry's body comes from `stage_body`, which moves from `queries/incident.py` to a
  shared `queries/stages.py` so both pages render stage output the same way.
- Do this: Retry when the run failed. Mark failed when the run is `PENDING` or stuck.
- Details: run ID, trace ID, fingerprint, the output refs, the last error with its type and
  retryable flag, and the stored payload.

Mark failed is for a run that's stuck or queued and should never run. It doesn't apply to a
finished run, and it doesn't apply to a run a drain is executing right now. Failing a live
run loses either way: the drain overwrites `FAILED`, or a Retry plus a drain runs it twice
and pages twice. `inbox.abandon(pk)` does it as one conditional update on
`inbox.abandonable()`, which is `status=PENDING` or `inbox.stuck()`, with error type
`ManualOverride` and "Marked failed by an operator". The run page offers it under the same
`Q`. A refused click says "This run is running or finished. Only a queued or stuck run can
be marked failed."

### `/console/inbox/`

The runs still waiting: `PENDING`, `PROCESSING` and every stuck run, oldest first, each with
its age. A stuck run is marked stuck. The status chips are all, waiting, in progress and
stuck.

- **Drain now** on a `PENDING` row calls `inbox.drain_run(run_id)`. It executes in the
  request, the way the admin's drain does, so it's one run per click.
- **Drain the oldest** drains up to `DRAIN_LIMIT = 10` through
  `inbox.drain_each(pks, seconds=DRAIN_SECONDS)`. With `DRAIN_SECONDS = 60` it starts no run
  after a minute, so the request ends well inside gunicorn's 120 second `--timeout` and
  doesn't strand the run it was executing. The notice says how many runs are left for the
  next drain.
- **Return stuck runs to the queue** calls `inbox.reclaim_stuck()`. The button shows only
  when a run is stuck.

Each outcome is a `Notice`, and each action is idempotent: `claim` refuses a run already
claimed, and reclaim touches only stuck rows. A drain that raises is caught per run and
reported as a count, as in the admin.

## Decision: a resume claims the run conditionally

`resume_pipeline` read the run as `FAILED`, then saved `RETRYING` without a condition and
executed. A console Retry plus a drain could claim the run in between. The move to
`RETRYING` is now one conditional update on the status and `updated_at` it read. A resume
that loses raises the existing `ValueError`, and `/orchestration/pipeline/<run_id>/resume/`
answers 409.

## Permissions

- Run pages read with `orchestration.view_pipelinerun` or `orchestration.change_pipelinerun`.
  Retry and Mark failed need `orchestration.change_pipelinerun`.
- The inbox reads with `orchestration.view_inboxitem` or `orchestration.change_inboxitem`.
  Drain and reclaim need `orchestration.change_inboxitem`. The admin's actions only needed the
  changelist, so the console is stricter.

## Wiring

- `view_run` points at `console:run`. `view_inbox` points at `console:inbox`, and its status
  param becomes the inbox page's filter. `Intent.permission` takes a tuple where any one
  suffices, so `view_run`, `view_inbox` and `view_alert` link for a user with the view or
  the change permission.
- The sidebar gains Runs and Inbox after Alerts.
- `apps/alerts/node_overview.py` links its incident and run rows to the console, and
  `config/dashboard.py` links the inbox card to `console:inbox`.

## Admin cut

- `PipelineRunAdmin`, `StageExecutionAdmin` and `InboxAdmin` become stock and read-only, with
  no add, change, delete or actions. `pipeline_flow`, the stage glyphs, `mark_for_retry`,
  `mark_for_retry_selected`, `mark_failed`, `drain_selected` and `reclaim_stuck` go.
- `IncidentAdmin` becomes stock and read-only. Its inlines, journey, timeline and the
  acknowledge, resolve and close actions go. The console incident page already carries all
  of them, and its run entries now open the console run page.
- `PipelineDefinitionAdmin` stays until PR 9.

## Housekeeping

The app has no Redis or Celery. `bin/lib/health_check.sh` still fails an install whose
`redis-server` isn't running, so that check goes.

## Tests

- `inbox.requeue` and `inbox.abandon`: each wins once, refuses the wrong status, and bumps
  attempts or writes the error. A requeued run that failed at notify re-runs only notify when
  drained.
- Queries: every filter, the node match, pagination and the stage entries for every attempt.
- Views: read and change permissions, 403 and 404, and each action's notice, including the
  lost-race refusal. Drain now runs exactly one run. Never drain a run that notifies a real
  channel in a test.
- `test_guard.py` covers the runs list, every run page and the inbox.
- Admin: the four admins are stock and read-only.
- 100% branch coverage on changed files.

## Docs

- `apps/orchestration/AGENTS.md`: the queue is the table, retry re-queues, and the console is
  the operator surface for runs.
- `apps/console/AGENTS.md`: the run and inbox pages and their permissions.
- `AGENTS.md`: the incident admin no longer carries the journey.

## Out of scope

- Routes (`PipelineDefinitionAdmin`), which is PR 9.
- Node pages, which is PR 8.
- A retry budget. `max_retries` is not enforced on an operator retry, as before.
