---
title: "Sysadmin Console PR 10: Reports Design"
parent: Plans
---

# Sysadmin console PR 10: reports

Domain slice 10 in `2026-10-15-sysadmin-console-foundation-design.md`. Check runs, preflight
runs and analyses get console pages. A Reports summary takes over the admin dashboard cards
the console lacks. `CheckRunAdmin`, `PreflightRunAdmin` and `AnalysisRunAdmin` become
stock and read-only. Nothing is exported today, and this PR adds no export.

## What exists

- `CheckRun` (`apps/checkers/models.py`): checker, host, status, message, metrics, error,
  thresholds, an optional alert, duration, `executed_at` and `trace_id`.
- `PreflightRun` with its `PreflightCheck` rows (level, message, hint), written by
  `manage.py preflight`.
- `AnalysisRun` (`apps/intelligence/models.py`): provider, model, a redacted
  `provider_config`, status, incident, the outputs, tokens and timings.
- Describers: `apps/checkers/describing.py`, `apps/intelligence/describing.py` and
  `apps/orchestration/describing.py`.
- The intents `view_preflight`, `view_check_run` and `view_preflight_run` still link the
  admin. The node page and `node_overview` use the last two.
- `get_dashboard_context` (`config/dashboard.py`) builds the admin dashboard cards:
  - active incidents and failed runs, which Overview and Runs already show
  - pipeline health over 24 hours
  - recent check runs
  - top failing checkers, top error types and provider usage over 7 days
  - `build_fleet_metrics`
- `RunFilter` (`apps/console/filters.py`) filters runs by status, origin, node, since and
  text.

## Navigation

A top-level Reports item with four tabs: Summary, Checks, Preflight and Analyses. A tab
shows only to a reader who has that model's read permission. The Reports item opens the
first tab the reader can read, the same way System does.

## Pages

### `/console/reports/`

Every row links to a filtered console list.

- **Pipeline health, last 24 hours:** the success rate, then failed, retrying and in
  flight. Each count opens Runs filtered to that status.
- **Top failing checkers, last 7 days:** each opens Checks for that checker, failing.
- **Top failure kinds, last 7 days:** each kind is worded by the orchestration describer
  and opens Runs filtered to that kind. `RunFilter` gains a `kind` filter.
- **Provider usage, last 7 days:** runs and tokens per provider, labelled from
  `PROVIDER_CHOICES`. Each opens Analyses for that provider.
- **Fleet metrics:** the `build_fleet_metrics` grid, with node links.

Active incidents and failed runs aren't repeated here. Recent check runs becomes the Checks
list. This summary needs the run read permission plus one of the three report permissions.
A card whose model the reader can't read is left out.

### `/console/checks/` and `/console/checks/<pk>/`

- The list filters by status, checker, node and since, and has a search box. It's
  paginated like Runs.
- Each row shows the checker's describer headline, the host, when it ran and how long it
  took.
- The record page shows the description, the thresholds, a link to the alert, and a link
  to the run found by `trace_id`. Raw metrics and the error go in Details.
- `view_check_run` resolves here.

### `/console/preflight/` and `/console/preflight/<pk>/`

- The list shows runs newest first, with a tone, the counts in words, who triggered it and
  the instance.
- The record page groups the checks by level, error first, each with its hint. A check's
  message is text the preflight command wrote on that machine, so it passes through as
  written.
- `view_preflight` and `view_preflight_run` resolve here. The readiness preflight row
  links the console.

### `/console/analyses/` and `/console/analyses/<pk>/`

- The list filters by status, provider and since.
- Each row shows the incident, the provider label, the outcome from `describe_analysis`
  and the tokens.
- The record page shows the summary, probable cause, recommendations, tokens and duration.
  Details holds `provider_config`, which is redacted before storage, and the input refs.

## Read-only

These pages write nothing, so there are no stale saves, no 409s and no deletes. Reads need
`view_*` or `change_*` on the model.

## Cut to stock

`CheckRunAdmin`, `PreflightRunAdmin` and `AnalysisRunAdmin` become stock and read-only.
`AnalysisRunAdmin` loses its `JSONEditorWidget` override and that override's test. The
card queries move to `apps/console/queries/reports.py`. The admin dashboard calls them and
links the console until PR 11 deletes the dashboard.

## Tests

- Each list filter, including bad values dropped and named.
- A 403 for each missing permission, and a reader of one report sees only that tab and
  card.
- The summary's counts and links, with an empty install too.
- The console guard covers every new page and filter.
- 100% branch coverage on changed code.

## Out of scope

- Running preflight from the console.
- Retention or pruning of check, preflight and analysis rows.
- Exports.
