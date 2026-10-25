---
title: "Sysadmin Console PR 10: Reports"
parent: Plans
---

{% raw %}

# Sysadmin console PR 10: reports

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Check runs, preflight runs and analyses get read-only console pages. A Reports
summary takes over the admin dashboard cards the console lacks. `CheckRunAdmin`,
`PreflightRunAdmin` and `AnalysisRunAdmin` drop to stock and read-only.

**Architecture:** Each app keeps describing its own rows:
- `apps/checkers` words check runs and preflight checks.
- `apps/intelligence` words analysis rows.
- `apps/orchestration` words failure reasons.

`apps/console` owns the filters, the reads and the pages. The pages mirror the Runs list
and the run page, since all three record types are read-only histories with filters. The
dashboard card queries move to `apps/console/queries/reports.py`, and the admin dashboard
calls them until PR 11 deletes it. Design:
`docs/plans/2026-10-25-sysadmin-console-pr10-reports-design.md`.

**Tech stack:** Django 5.2 views and templates, and pytest-django.

**Rules for every task:**
- Use absolute imports. No comments that restate code. Line length is 100.
- No wording on a page carries a UUID, a `snake_case` token or a `{` outside Details.
- A checker renders through `checker_label`, and a provider through its
  `PROVIDER_CHOICES` label.
- Run `uv run black . && uv run ruff check . --fix` before each commit.
- Pre-commit runs black, ruff, the full pytest suite and mypy, so a commit takes 20 minutes
  or more. Run it in the background.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Voice: no em or en dashes. None of the words gate, seam, wire, leverage, crucial,
  navigate or align.

The lists copy the Runs list:
- `apps/console/views/runs.py` and `apps/console/queries/runs.py`
- `RunFilter` in `apps/console/filters.py`
- `templates/console/runs.html` and its partials

The record pages copy the run page:
- `apps/console/views/run.py` and `apps/console/queries/run.py`
- `templates/console/run.html`

Read those first. Keep their behaviour:
- htmx list refresh and pagination
- bad filter values dropped and named
- a 403 per missing permission and the console 404 page
- Details for raw values

## Where this plan corrects the design

1. `PipelineRun.last_error_type` holds an exception class name such as
   `StageExecutionError`, not a classified kind. The card becomes **Top failure reasons**:
   - Failed runs group by `reason_code(last_error_message)` from
     `apps/orchestration/describing.py`.
   - Each group is worded by its `REASONS` headline, capitalised.
   - Runs with no known code group as "Other failures".
   - `RunFilter` gains `reason`, which is a `REASONS` key, or `other`.
2. Pipeline health uses the Runs status chips. "Finished" is `finished`, "Failed" is
   `failed`, and "Still going" is `active`, which covers retrying and in flight. Each link
   carries `since=24h`.
3. `CheckRun` has no node key, only `hostname`. Checks filter by `host`, chosen from the
   distinct hostnames. The record page links the node when exactly one `Node` has that
   hostname.
4. `AnalysisRun` stores no failure kind. `describe_analysis_run` words a failed row as
   `FAILURES["unknown"]`, with the stored error as a fact.
5. `AnalysisRun.provider` can be `local`, which isn't in `PROVIDER_CHOICES`. It reads
   "Built-in local analyzer". Any other unknown key reads "A provider this hub doesn't
   have".

---

### Task 1: Report filters

**Files:**
- Modify: `apps/console/filters.py`
- Test: `apps/console/_tests/test_filters.py`

Add three frozen dataclasses shaped like `RunFilter`, with `parse`, `querystring` and
`dropped`:
- `CheckFilter`: `q`, `status`, `checker`, `host`, `since` and `page`.
  - `status` takes `""`, `failing` (warning, critical or unknown), or a `CheckStatus`
    value.
  - `checker` is a `CHECKER_REGISTRY` key, or a name already stored.
  - `host` is free text, capped at `SOURCE_LIMIT`.
- `PreflightFilter`: `status` (`""`, `ok`, `warn` or `error`), `since` and `page`.
- `AnalysisFilter`: `status` (`""` or an `AnalysisRunStatus` value), `provider`, `since`
  and `page`.

`RunFilter` gains `reason`, which takes `""`, `other` or a `REASONS` key. `matches()`
turns a code into `Q(last_error_message__regex=...)`, the same pattern `_reason` reads,
and turns `other` into failed runs with none of those codes. Add `reason` to
`RUN_FIELDS`.

Tests: each value parses, each bad value is dropped and named, querystrings round-trip,
and `reason` filters the runs it names.

Commit `feat(console): filters for checks, preflight, analyses and run reasons`.

---

### Task 2: Check runs

**Files:**
- Modify: `apps/console/intents.py`
  - `CHECK_READ = ("checkers.view_checkrun", "checkers.change_checkrun")`
  - `PREFLIGHT_READ` and `ANALYSIS_READ` in the same shape
  - `view_checks` is a `_filtered("console:checks", CheckFilter)` intent
  - `view_check_run` now uses `_record("console:check")`
- Create: `apps/console/queries/checks.py` and `apps/console/queries/check.py`
- Create: `apps/console/views/checks.py` and `apps/console/views/check.py`
- Create: `templates/console/checks.html`, `check.html`, `partials/_checks.html`,
  `partials/_reports_nav.html` and `partials/_reports_item.html`
- Modify: `apps/console/urls.py`, `views/__init__.py`, `templates/console/base.html` and
  `not_found.html`
- Test: `apps/console/_tests/queries/test_checks.py`, `_tests/views/test_checks.py` and
  `_tests/views/test_check.py`

URLs:
- `checks/` → `console:checks`
- `checks/<pk>/` → `console:check`

**Nav:**
- `base.html` gains a Reports item after Nodes, using the `graph` octicon and the same
  first-readable-tab chain as System.
- `_reports_nav.html` holds the tabs. This task adds Checks. Tasks 3 to 5 add Preflight,
  Analyses and Summary, and put Summary first in both the chain and the tabs.

**The list:**
- Rows are newest first.
- Each row shows the tone icon and `describe_check(...)`'s headline.
- The meta line reads `<checker label> · <host> · <relative time> · <duration>`.
- The filters are status chips, a checker select, host, since and search. Search covers
  the message and the host.

**The record page:**
- The description and its detail.
- Thresholds read "Warns at <n>, critical at <n>", leaving out a side that's unset.
- "Raised alert" links `view_alert` when there's an alert.
- "Part of a run" links `view_runs` with `q=<trace_id>` when there's a trace.
- The node link follows correction 3.
- Details holds the metrics, the error, the trace and the raw status.

Tests:
- Each filter.
- The headline comes from the checker's describer, and an unknown checker reads as "the
  node".
- The 403 and the 404.
- A view-only reader sees no dead link.
- The node page's "Open the check run" now opens the console.

Commit `feat(console): check runs`.

---

### Task 3: Preflight runs

**Files:**
- Create: `apps/checkers/preflight_describing.py`
  - `describe_preflight_run(run) -> Description`. "Preflight passed", "Preflight found
    <n> warnings" or "Preflight found <n> errors", the same wording as the dashboard's
    `_preflight`, with tone ok, warning or critical.
  - `describe_preflight_check(check) -> Description`. The headline is the stored message,
    the detail is the hint, and the tone comes from the level. An unknown level reads
    muted.
- Modify: `config/dashboard.py`. `_preflight` uses `describe_preflight_run`, and the
  readiness `url` becomes `reverse("console:preflight")`.
- Modify: `apps/console/intents.py`
  - `view_preflight` uses `_page("console:preflight")`
  - `view_preflight_run` uses `_record("console:preflight-run")`
- Create: queries, views and templates mirroring Task 2, as `preflight` and
  `preflight_run`.
- Test: mirroring Task 2, plus `apps/checkers/_tests/test_preflight_describing.py`

URLs:
- `preflight/` → `console:preflight`
- `preflight/<pk>/` → `console:preflight-run`

**The list:** each row shows the tone icon, the headline and `<triggered by> · <instance>
· <relative time>`, leaving out blank parts.

**The record page:** the checks group under "Errors", "Warnings", "Info" and "Passed", in
that order. An empty group is left out.

Tests:
- Each level and count wording.
- The readiness row and its keyed description now open the console.
- The node page's preflight link opens the console.
- The 403 and the 404.

Commit `feat(console): preflight runs`.

---

### Task 4: Analyses

**Files:**
- Modify: `apps/intelligence/describing.py`
  - `provider_label(key) -> str`, per correction 5
  - `describe_analysis_run(run) -> Description`
    - Pending and running rows read "AI analysis is still running", with tone info.
    - Failed rows follow correction 4.
    - Succeeded rows give `describe_analysis` a snapshot built from the row: `summary`,
      `probable_cause`, `confidence`, `fallback_used`, `failure: ""` and
      `model_info.provider` set to the label.
  - `describe_recommendations(items) -> tuple[Description, ...]`
    - Each item's title is the headline and its description is the detail.
    - Priority critical or high reads warning, and medium or low reads info.
    - Items that aren't a dict, or have no title, are skipped.
- Modify: `apps/console/intents.py`
  - `view_analyses` is a `_filtered("console:analyses", AnalysisFilter)` intent
  - `view_analysis` uses `_record("console:analysis")`
- Create: queries, views and templates mirroring Task 2, as `analyses` and `analysis`.
- Test: mirroring Task 2, plus new cases in `apps/intelligence/_tests/test_describing.py`

URLs:
- `analyses/` → `console:analyses`
- `analyses/<pk>/` → `console:analysis`

**The list:** each row shows the tone icon, the headline and `<incident subject, linked
with view_incident> · <provider label> · <tokens> tokens · <relative time>`. The filters
are status chips, a provider select and since.

**The record page:**
- The description.
- Recommendations as a list of descriptions.
- Tokens in words: "<prompt> in, <completion> out".
- Duration through `format_duration`.
- Details holds `provider_config`, `input_summary`, `checker_output_ref`, the trace and the
  pipeline run id.

Tests:
- Each status.
- An unknown provider.
- Malformed recommendations.
- A row that never had an incident.
- The 403 and the 404.
- The stored `provider_config` renders only inside Details.

Commit `feat(console): analyses`.

---

### Task 5: The Reports summary

**Files:**
- Create: `apps/console/queries/reports.py`
  - `pipeline_health(now)`
  - `failing_checkers(now)`
  - `failure_reasons(now)`
  - `provider_usage(now)`
  - `build_reports(user)`, which leaves out a card the user can't read
- Modify: `config/dashboard.py`. `get_dashboard_context` takes its trend rows from those
  functions, and each row's `url` now points at the console list.
- Create: `apps/console/views/reports.py`, `templates/console/reports.html` and
  `partials/_reports.html`
- Test: `apps/console/_tests/queries/test_reports.py`, `_tests/views/test_reports.py`, and
  the dashboard tests that pin the old URLs.

URL: `reports/` → `console:reports`.

Cards:
- **Pipeline health, last 24 hours:** "<rate> of runs finished", then Finished, Failed and
  Still going, each linking `view_runs`, per correction 2. An empty window reads "No runs
  in the last 24 hours."
- **Top failing checkers, last 7 days:** up to 5 rows of `<checker label>` and "<n>
  failing results". Each links `view_checks` with `checker` and `status=failing`.
- **Top failure reasons, last 7 days:** up to 5 rows, per correction 1. Each links
  `view_runs` with `status=failed`, `reason` and `since=7d`.
- **Provider usage, last 7 days:** `<label>`, then "<n> analyses, <tokens> tokens". Each
  links `view_analyses` with `provider` and `since=7d`.
- **Fleet metrics:** the `build_fleet_metrics` grid. The column heads use
  `checker_label`, and the units come from `METRIC_UNITS`.

The page needs `RUN_READ`, or any of `CHECK_READ`, `PREFLIGHT_READ` and `ANALYSIS_READ`.
Fleet metrics show to a reader with `NODE_READ`.

Tests:
- Each card's counts and links.
- Each card left out for a reader without its permission.
- An empty install.
- The 403.

Commit `feat(console): a reports summary`.

---

### Task 6: Cut the admins

**Files:**
- Modify: `apps/checkers/admin.py`. `CheckRunAdmin` and `PreflightRunAdmin` become stock
  and read-only:
  - `list_display` uses plain fields.
  - Add, change and delete all return False.
  - The docstring reads "Read-only. Operators read check runs in the console.", or the
    preflight equivalent.
  - The badges, the links, `pretty_metrics` and the inline go.
- Modify: `apps/intelligence/admin.py`. `AnalysisRunAdmin` gets the same treatment, and
  `exclude = ["provider_config"]`. Its `formfield_overrides`, `pipeline_run_link` and
  `pretty_*` methods go.
- Delete the mapping test that pinned `JSONEditorWidget`. Remove `prettify_json` and
  `JSONEditorWidget` imports nothing uses any more.
- Test: each admin refuses add, change and delete, and its changelist still renders.

Commit `feat!: the check, preflight and analysis admins are stock and read-only`.

---

### Task 7: The guard covers reports

**Files:**
- Modify: `apps/console/_tests/fixtures.py` and `apps/console/_tests/test_guard.py`

Fixtures:
- A check run per `CheckStatus`, one with an alert and a trace.
- One check run for a checker the hub doesn't have.
- Two preflight runs, one with an error, a warning and an unknown level.
- Analyses: one succeeded with recommendations, one failed, one pending, one from `local`,
  and one from an unknown provider.
- A failed run with a `no_route` reason and one with none.

`World` gains `check_pks`, `preflight_pks` and `analysis_pks`.

Guard URLs:
- `reports/`
- every list, unfiltered and with each filter value
- every record page

Shown strings:
- "of runs finished"
- "Top failure reasons"
- "Built-in local analyzer"
- "A provider this hub doesn't have"
- "Other failures"

The stored `provider_config` appears only inside `data-details`.

Commit `test(console): the guard covers reports`.

---

### Task 8: Docs

- `AGENTS.md`: convention 3 adds check runs, preflight runs and analyses to what's read in
  the console.
- `apps/console/AGENTS.md`: the Reports bullet, its pages and intents.
- `apps/checkers/AGENTS.md`: the preflight describer and the admin expectations.
- `apps/intelligence/AGENTS.md`: `describe_analysis_run`, `describe_recommendations` and
  the admin expectations.
- `docs/Architecture.md`: the admin table.
- Any `docs/` page or README that sends operators to these admins. Not `docs/plans/`.

Commit `docs: reports live in the console`.

{% endraw %}
