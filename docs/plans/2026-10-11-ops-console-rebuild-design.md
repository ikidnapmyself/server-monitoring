---
title: "Ops Console Rebuild"
parent: Plans
---

# Ops console rebuild

## Problem

The operator surface is stock `django.contrib.admin` under `MonitoringAdminSite`, with
about 2,100 lines of `ModelAdmin` code, four custom pages and the ops skin from #243.
The skin changed colours and spacing but kept Django's changelist and changeform
structure. That structure is the problem. Operators can't read an incident as a story,
can't see a period at a glance, and can't export anything.

A second constraint drives the technology choice. The owner doesn't want an npm
dependency tree to keep patched, which rules out the existing admin replacements and
any bundled frontend.

## Decision

Build a new `apps/console` app at `/console/` with its own templates and CSS. Keep
`django.contrib.admin` at `/admin/` as the raw CRUD editor for configuration rows. The
console shares no markup or CSS with the admin.

Rendering is server-side Django templates. Partial updates use one vendored htmx file.
There is no `package.json`, no bundler and no node in CI or the Docker image.

### Alternatives rejected

- **More custom views inside `MonitoringAdminSite`.** Cheapest, but every page inherits
  `base_site.html` and the admin CSS that #243 couldn't rescue.
- **JSON API plus a JS-rendered client.** Needs a hand-written client framework without
  npm and duplicates every permission check.
- **Replacing the admin entirely.** Hand-built CRUD for every config model is a large
  build with no operator benefit.

## Inventory of what already exists

| Need | Existing capability | Use |
|---|---|---|
| Dashboard and readiness data | `config/dashboard.py` | Move into `apps/console/queries/` |
| Routing map data | `config/netmap.py` | Move into `apps/console/queries/` |
| Tone vocabulary | `config/admin_badges.py` | Reuse the tone names |
| Incident timeline | `apps/alerts/timeline.py` `build_incident_timeline` | Extend and render, don't rewrite |
| Stage diagnosis | `apps/alerts/diagnosis.py` `diagnose_incident` | Reuse on the incident and run pages |
| Node page data | `apps/alerts/node_overview.py` | Reuse for the node page |
| Policy page data | `apps/alerts/policy_overview.py` | Reuse for System |
| SVG sparkline | `apps/checkers/admin_charts.py` `render_sparkline` | Reuse. Add bar charts beside it |
| Admin deep links | `config/admin_links.py` | Reuse for every "Edit raw" link |
| Incident transitions | `IncidentManager` in `apps/alerts/` | Call from action views |
| Run retry and mark-failed | `PipelineRunAdmin` change actions | Lift into `apps/orchestration`, call from both surfaces |
| Re-evaluate | `AlertAdmin` and `NodeAdmin` flows | Lift into `apps/alerts`, call from both surfaces |
| Auth | Django session login and model permissions | Reuse unchanged |

No new models, migrations, endpoints for machines, or write paths.

## Pages

Eight sidebar entries. Each page answers one operator question.

| Page | URL | Answers |
|---|---|---|
| Overview | `/console/` | Is anything wrong right now, and is the system itself healthy? |
| Incidents | `/console/incidents/` | What's open, what changed, what needs me? |
| Incident | `/console/incidents/<id>/` | What happened, how do we know, what did we do about it? |
| Alerts | `/console/alerts/` and `/<id>/` | Which alerts fired, and what did each one say? |
| Runs | `/console/runs/` and `/<id>/` | What did the pipeline do, stage by stage? |
| Nodes | `/console/nodes/` and `/<id>/` | Which machines are unhealthy or quiet, and what's this one's history? |
| Reports | `/console/reports/` | What did the last 24h, 7d or 30d look like? |
| System | `/console/system/` | Routing map, policy, inbox, channel and provider readiness. |

`/` redirects to `/console/`.

**Overview.** A status sentence in words at the top. Below it: open incidents sorted by
severity then age, a node health grid with one tile per node, and a 24h incident
sparkline. The readiness panel is one line when green and expands only when something
is amber or red. It refreshes every 30 seconds.

**Incidents.** One row per incident: severity dot, title, node, age, status, alert
count. Filter chips for status, severity, node and time range update the list in place
and live in the URL.

**Incident.** Header with title, severity, status, duration and action buttons. Then
one vertical timeline that merges alerts, status changes, pipeline runs, checker
output, analysis and notifications in chronological order. Each entry is one readable
line that expands for detail. Raw JSON is collapsed by default. A print stylesheet
renders the page as a report.

**Alerts and Runs.** List and detail each. The incident timeline links into them.

**Nodes.** A tile grid, then a per-node page with current check results, open
incidents, 30-day alert volume and the policy in effect.

**Reports.** A range picker, then incidents by severity, node and checker,
time-to-acknowledge, time-to-resolve, noisiest fingerprints and notification delivery
outcomes.

**Export.** Every list and report table has CSV and JSON links that honour the current
filters.

## Architecture

```
apps/console/
  views/        overview.py incidents.py alerts.py runs.py nodes.py reports.py system.py export.py
  queries/      one module per page, plain functions returning frozen dataclasses
  filters.py    parse and validate a querystring into a typed filter object
  urls.py  AGENTS.md  _tests/
templates/console/   base.html, one template per page, partials/_*.html
static/console/      console.css, console.js, vendor/htmx.min.js, vendor/VENDOR.md
```

**Request flow.** A view parses the querystring through `filters.py`, calls one
`queries/` function and renders a template. On an htmx request the same view renders
only the partial. One URL and one query path serve both sizes, so every state is
linkable and every page works with JavaScript off.

**Reads.** `queries/` is the only place that touches the ORM. Functions return
dataclasses, never querysets, so a template can't trigger a lazy query. The incident
timeline comes from the existing `build_incident_timeline`, extended with alert and
notification entries where it lacks them.

**Writes.** Action views are POST-only, CSRF-protected and permission-checked. They
call the same functions the admin calls. Logic that lives inside a `ModelAdmin` method
today is lifted into the owning app first, and the admin action is changed to call it.

**Boundaries.** `apps.console` imports from the other apps. Nothing imports from
`apps.console`. It is a consumer, not a stage and not a producer.

**Auth.** One mixin requires `is_staff` on every view. Destructive actions also check
the model permissions the admin checks.

## Visual system

One `console.css` built on CSS custom properties, no preprocessor.

- System font stack. No font files.
- A 4px spacing scale, one radius, one shadow level. Content column capped near 1200px.
- Neutral greys carry the interface. Colour appears only for severity and status.
- Light and dark from `prefers-color-scheme`. AA contrast is checked by test.
- Timestamps render relative, with the absolute time on hover. Durations read as
  "4m 12s". Identifiers are truncated with a copy button.
- Each stage's output gets a small purpose-built renderer. Raw JSON sits behind a
  disclosure.
- Motion is limited to 150ms fades on swaps and is disabled under
  `prefers-reduced-motion`.

**Charts.** Server-rendered SVG. Sparklines reuse `render_sparkline` from
`apps/checkers/admin_charts.py`. Stacked bars and the severity-by-day strip are added
beside it in PR 6. Hover values use native `<title>` elements. There is no chart
library.

## Frontend dependency policy

- No `package.json`, no bundler, no node.
- `static/console/vendor/htmx.min.js` is the only third-party file.
- `vendor/VENDOR.md` records its version, upstream URL and SHA-256.
- A test hashes the file and compares it with the recorded value.
- A test asserts the vendor directory holds exactly the files `VENDOR.md` lists.
- `console.js` is first-party and targets under 300 lines.
- Every console response carries a CSP of `default-src 'self'`, `script-src 'self'`,
  `style-src 'self'`. No inline script or style, no eval. htmx's eval and inline-style
  features are disabled in its config.
- A scheduled GitHub Action compares the pinned htmx version with the upstream
  repository's security advisories and fails when one applies.
- An upgrade replaces one file, updates `VENDOR.md` and runs the suite.

## Errors and edge cases

- Invalid filter input is dropped, defaults apply, and a one-line notice says so.
- Every list is paginated. Report ranges cap at 90 days.
- Exports stream and cap at 50,000 rows, with a visible notice when truncated.
- Empty states are written per page and distinguish "none exist" from "none match".
- A failed htmx request leaves the old content and shows a retry banner.
- An expired session on an htmx request triggers a full redirect to login.
- An action on a stale state re-renders the page with current state and a message.
- Live refresh pauses while a disclosure is open or the tab is hidden.
- Payloads render as the same redacted refs the admin shows.

## Known limitation

The refire-staleness question in `2026-09-14-refire-staleness-open-question.md` is
still open. A refired incident shows its first-fire title, description and timestamps.
The console renders what the model holds and doesn't work around this.

## Testing

- `queries/` has unit tests with query-count assertions.
- Every view is tested for anonymous redirect, non-staff 403, full render and partial
  render.
- Every action view is tested for GET rejected, missing permission rejected and
  delegation to the existing function.
- New chart renderers are tested on empty, single-point and normal data.
- Guard tests cover the CSP header, absence of inline script and style in console
  templates, the vendor hash, the vendor directory listing and contrast pairs.
- 100% branch coverage on new code.

## Delivery

Six PRs. Each ships something usable.

1. Skeleton: app, base template, CSS tokens, auth mixin, CSP, vendored htmx with its
   guards and advisory check, Overview page.
2. Incidents list and incident detail with the timeline, read-only.
3. Operator actions, with logic lifted out of `ModelAdmin` methods.
4. Alerts and Runs, list and detail.
5. Nodes and System, moving map, policy and readiness over.
6. Reports, charts and exports. Then retire the admin dashboard, the ops skin and
   `SECTION_MAP`, leaving `/admin/` as stock Django.

## Acceptance criteria

- An operator can go from Overview to a full incident story without opening `/admin/`.
- Every list state is reachable by URL and exportable as CSV and JSON.
- The repository contains no `package.json` outside `bin/tests/test_helper/`.
- Every console page links onward. No dead ends.
- After PR 6 there is one ops surface, and `/admin/` carries no custom dashboard code.
