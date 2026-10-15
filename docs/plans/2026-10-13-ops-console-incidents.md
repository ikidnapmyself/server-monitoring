---
title: "Ops Console Incidents Implementation Plan"
parent: Plans
---

{% raw %}

# Ops console incidents implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task by task.

**Goal:** Ship PR 2 of the console rebuild: a filterable incident list and a read-only
incident page that tells the incident as one timeline.

**Architecture:** Two new `ConsoleView` pages on the shapes PR 1 set. A typed filter
object parses the querystring. Two read-model modules return frozen dataclasses. The
incident page reuses `build_incident_timeline` and `diagnose_incident` and adds only
what they lack.

**Tech stack:** Django 5.2 templates, the vendored htmx 2.0.11, `console.css`,
`console.js`, pytest-django.

Design: `2026-10-11-ops-console-rebuild-design.md`. Rules for the app:
`apps/console/AGENTS.md`. This plan states interfaces, behaviour and tests. It doesn't
print the code, because the executor can read PR 1's modules for the shapes.

## Ground rules

- Branch `feat/console-incidents`, cut from `feat/console-skeleton`. Never commit to
  `main`. No worktrees.
- A commit runs the full suite and mypy through pre-commit and takes several minutes.
  Never pass `--no-verify`.
- TDD. Write the failing test, watch it fail, then write the code.
- Absolute imports. Line length 100. No comments that restate the code.
- No inline script, style, `style=`, `on*=` or `hx-on` in console templates.
- No hex colours outside `static/console/console.css`.
- Views never touch the ORM. `queries/` returns frozen dataclasses with tuples, never
  querysets or lists.
- Every focusable element inside a polled region carries a stable unique `id`.
- 100% branch coverage on `apps/console` and on changed lines elsewhere.

## Out of scope

Operator actions are PR 3. Alert and run pages are PR 4, so alert and run links on the
incident page go to the admin through `config/admin_links.py` or `reverse("admin:...")`.
Node pages are PR 5, so the node filter exists but node names don't link. Exports are
PR 6.

---

### Task 1: The incident filter

**Files:** create `apps/console/filters.py` and `apps/console/_tests/test_filters.py`.

`IncidentFilter` is a frozen dataclass:

| Field | Type | Values | Default |
|---|---|---|---|
| `status` | `str` | `active`, `open`, `acknowledged`, `resolved`, `closed`, `all` | `active` |
| `severity` | `str` | `""`, `critical`, `warning`, `info` | `""` |
| `node` | `int \| None` | a `Node` pk | `None` |
| `since` | `str` | `""`, `24h`, `7d`, `30d` | `""` |
| `page` | `int` | 1 or more | `1` |
| `dropped` | `tuple[str, ...]` | names of params that failed validation | `()` |

`active` means open plus acknowledged. Severity values come from `AlertSeverity`.

- `IncidentFilter.parse(params: QueryDict) -> IncidentFilter`. An invalid value falls
  back to the default and its param name lands in `dropped`. Unknown params are
  ignored and not reported. `node` must be a positive integer. It isn't checked
  against the database here. `page` below 1 or non-numeric is dropped.
- `statuses() -> tuple[str, ...]`. The `IncidentStatus` values the filter selects.
  Empty for `all`.
- `cutoff(now) -> datetime | None`.
- `querystring(**changes) -> str`. Returns `""` or `"?a=b"`. Emits only non-default
  values, in a fixed field order, URL-encoded. Changing any field other than `page`
  resets `page` to 1. `dropped` is never emitted.
- `with_node_dropped() -> IncidentFilter`. Used by the query when the node pk doesn't
  exist.

Tests: defaults from an empty querystring, each field's valid values, each field's
invalid value landing in `dropped`, `querystring` round trip through `parse`, page
reset on change, encoding of nothing unsafe because every emitted value is validated.

Commit: `feat(console): typed incident filter`.

### Task 2: Incident list page

**Files:**

- Create `apps/console/queries/incidents.py`, `apps/console/views/incidents.py`,
  `templates/console/incidents.html`, `templates/console/partials/_incidents.html`.
- Modify `apps/console/urls.py`, `apps/console/views/__init__.py`,
  `apps/console/queries/overview.py`, `templates/console/base.html`,
  `static/console/console.css`.
- Tests: `apps/console/_tests/queries/test_incidents.py`,
  `apps/console/_tests/views/test_incidents.py`, plus updates to the Overview tests.

**Read model.** `build_incident_list(filter: IncidentFilter) -> IncidentList`.

- `IncidentListRow`: `pk`, `title`, `severity`, `severity_tone`, `status`,
  `status_tone`, `nodes` as a display string, `created_at`, `alert_count`, `url`.
  `nodes` is the first node name by casefold order, plus `+N` when more nodes exist,
  or `""` when no alert has a node.
- `Chip`: `key`, `label`, `url`, `active`. `ChipGroup`: `name`, `label`, `chips`.
  Groups: status, severity, node, since. Each chip's URL is the list URL plus
  `filter.querystring(field=value)`. The node group lists every `Node` by name and is
  omitted when no nodes exist.
- `IncidentList`: `rows`, `total`, `page`, `pages`, `prev_url`, `next_url`, `groups`,
  `notice`, `empty`, `url`. `url` is the list URL with the current querystring, used
  for polling. `notice` is `""` or `Ignored invalid filter: a, b.`. `empty` is `""`,
  `"none"` when no incident exists at all, or `"no_match"`.
- Page size 50. A page past the end clamps to the last page.
- Order: severity rank, then newest `created_at` first. Reuse the rank expression from
  `queries/overview.py`. Make it a public name there.
- The node filter uses an `Exists` subquery on `Alert`, not a join, so the alert count
  annotation stays correct. A node pk that doesn't exist is treated as an invalid
  filter: dropped, named in the notice.
- Query count is constant for any page size: count, page rows, node names for the
  page, node chips, and the any-incident check only when the page is empty. Assert the
  number with `django_assert_num_queries`.

**View.** `IncidentListView(ConsoleView)` at `incidents/`, name `console:incidents`,
`nav = "incidents"`.

**Templates.** The partial's root is the polled element, so its `hx-get` always
matches the current filters:

```html
<div id="incidents" data-poll hx-get="{{ list.url }}"
     hx-trigger="every 30s, console:refresh" hx-swap="outerHTML">
```

Chips and pager links are plain `<a href>` with `hx-get` to the same URL,
`hx-target="#incidents"`, `hx-swap="outerHTML"` and `hx-push-url="true"`. They work
with JavaScript off. Ids: `chip-<group>-<key>`, `incident-<pk>`, `page-prev`,
`page-next`. The active chip carries `aria-current="true"`. Rows show severity badge,
title link, node string, alert count, status badge and relative age, in the Overview
row markup. Empty states: `none` reads "No incidents yet.", `no_match` reads "No
incidents match these filters." with a link that clears them.

**Sidebar and Overview.** Add the Incidents entry to `base.html`. In
`queries/overview.py`, `more_url` becomes `console:incidents`. Rows on both pages keep
their admin links until Task 4 adds the incident page.

**Tests.** Anonymous redirect, non-staff 403, full page, partial, the `FORBIDDEN` scan
over rendered full page and partial, a hostile title and hostname rendered escaped,
each filter narrowing the rows, the notice, both empty states, pagination links
keeping filters, the constant query count.

Commit: `feat(console): incident list with filter chips`.

### Task 3: Incident read model

**Files:**

- Modify `apps/alerts/timeline.py`, `apps/alerts/_tests/test_timeline.py`.
- Create `apps/console/formatting.py`, `apps/console/queries/incident.py`.
- Tests: `apps/console/_tests/test_formatting.py`,
  `apps/console/_tests/queries/test_incident.py`.

**Timeline, additive only.** The admin renders these dicts, so no key is renamed or
removed and no new `kind` is added.

- `alert_history` entries gain `alert_pk`, `alert_name` and `event`. The query gains
  `select_related("alert")`.
- `pipeline` entries gain `run_pk` and `status`.
- `stage` entries gain `stage`, `status`, `execution_pk`, `run_pk`, `duration_ms` and
  `output_snapshot`.

**Formatting.** `humanize_duration(seconds: float) -> str` gives `4m 12s`, `3h 05m`,
`2d 4h`, `850ms` under one second, and `0s` for zero or negative.

**Read model.** `build_incident_detail(pk: int) -> IncidentDetail | None`.

- `IncidentDetail`: `pk`, `title`, `severity`, `severity_tone`, `status`,
  `status_tone`, `description`, `summary`, `created_at`, `duration`, `ongoing`,
  `nodes`, `pipeline`, `alert_count`, `admin_url`, `stages`, `timeline`.
  `duration` runs from `created_at` to `resolved_at`, else `closed_at`, else now.
- `StageSummary`, one per `diagnose_incident` entry: `stage`, `label`, `status`,
  `tone`, `detail`, `runs`, `url`. Labels: check is "Diagnose", analyze is "Analyze",
  notify is "Notify", ingest is "Ingest". Tones: ok is `ok`, failed is `critical`,
  stalled and empty are `warning`, skipped and never_ran are `muted`. `url` is the
  admin stage execution page when `execution_pk` is set, else `""`.
- `TimelineEntry`: `id`, `when`, `kind`, `tone`, `title`, `detail`, `url`, `body`.
  `id` is stable and unique: `entry-<kind>-<pk>`.
- Entries come from `build_incident_timeline`, plus incident lifecycle entries the
  console builds itself from `created_at`, `acknowledged_at`, `resolved_at` and
  `closed_at`, with kind `incident`. Sorted by `when`, lifecycle first on a tie.
- Titles read as sentences. Alert history: `Alert <name> <event>`, linking to the
  admin alert. Run: `Pipeline run <status>`, linking to the admin run. Stage:
  `<Label> <status>` with the duration when present.
- `body` is `None` or one of three frozen dataclasses built from `output_snapshot`,
  each with a `raw` field holding `json.dumps(snapshot, indent=2, sort_keys=True,
  default=str)`:
  - `CheckBody`: `lines` of `CheckLine(name, status, tone, message)`, `run`, `passed`,
    `failed`, `errors`.
  - `AnalyzeBody`: `summary`, `probable_cause`, `actions`, `recommendations` of
    `Recommendation(title, priority, description)`, `confidence` as a percent integer,
    `fallback_used`, `errors`.
  - `NotifyBody`: `deliveries` of `Delivery(provider, channel, status, tone,
    provider_id)`, `attempted`, `succeeded`, `failed`, `errors`.
  - A stage with a snapshot of any other shape gets `RawBody(raw)`.
- Every body builder tolerates missing keys and wrong types. A snapshot is stored
  JSON, so a list where a dict is expected must not raise.
- Query count is constant in the number of alerts, history rows, runs and stages.

**Tests.** Timeline additive keys. Formatter boundaries. Detail for a missing pk,
header fields, duration for open and resolved incidents, lifecycle entries, ordering,
each body from a realistic snapshot, each body from a malformed snapshot, tones, the
constant query count.

Commit: `feat(console): incident read model with stage renderers`.

### Task 4: Incident page

**Files:**

- Create `apps/console/views/incident.py`, `templates/console/incident.html`,
  `templates/console/partials/_incident.html`,
  `templates/console/partials/_stage_body.html`.
- Modify `apps/console/urls.py`, `apps/console/views/__init__.py`,
  `apps/console/queries/overview.py`, `apps/console/queries/incidents.py`,
  `static/console/console.css`, `static/console/console.js`.
- Tests: `apps/console/_tests/views/test_incident.py`, plus Overview and list updates.

**View.** `IncidentDetailView(ConsoleView)` at `incidents/<int:pk>/`, name
`console:incident`, `nav = "incidents"`. A missing incident raises `Http404`.

**Template.** Polled wrapper as on Overview, `hx-swap="innerHTML"`.

- Header: title, severity and status badges, started time, duration with "so far" while
  ongoing, node string, alert count, pipeline name, description, summary, and an
  "Edit raw" link with id `incident-admin` to `admin_url`. A "Back to incidents" link.
- Stage strip: one badge per `StageSummary` with its detail and runs line.
- Timeline: an ordered list. Each entry is one line with time, tone badge and title.
  An entry with a `body` is a `<details id="{{ entry.id }}">` whose summary is that
  line. Inside: the structured body, then a nested `<details>` holding the raw JSON in
  `<pre>`. Entries without a body are plain list items whose link carries the id.
- Raw JSON is rendered through Django autoescaping only. Never `|safe`.
- An empty timeline reads "Nothing recorded for this incident yet."

**Links.** Overview rows and list rows now point at `console:incident`.

**CSS.** Timeline, stage strip, chips reuse existing tokens. Add a `@media print` block:
hide the sidebar, banner, chips and links' underline, remove card shadows, avoid
breaking inside a timeline entry.

**JS.** `beforeprint` opens every `<details>` under `[data-print-open]` and remembers
which it opened. `afterprint` closes those again. Put `data-print-open` on the timeline
list. Keep `console.js` under 300 lines.

**Tests.** Anonymous redirect, non-staff 403, 404 for a missing pk carrying the console
CSP, full page, partial, the `FORBIDDEN` scan over both, a hostile title, alert name and
snapshot string rendered escaped, every entry id unique, each stage body rendered, the
empty timeline, Overview and list rows linking to the console page.

Commit: `feat(console): incident page with a merged timeline`.

### Task 5: Docs, verification, PR

- `apps/console/AGENTS.md`: the filter object, the outerHTML polling pattern for
  filtered lists, the stage bodies, the print hook.
- `apps/alerts/AGENTS.md`: the timeline's new keys and that the console depends on them.
- Run `uv run black . --check`, `uv run ruff check .`, `uv run mypy .`,
  `uv run bandit -r apps/ config/ -c pyproject.toml`, `uv run pip-audit --strict --desc`,
  `uv run coverage run -m pytest && uv run coverage report --include='apps/console/*,apps/alerts/timeline.py'`.
- Open the PR against `feat/console-skeleton`, or `main` if #246 has merged.

{% endraw %}
