# apps.console

The operator console at `/console/`. A consumer of the other apps: it is not a
pipeline stage and not an alert producer.

## Rules

- **Nothing imports from `apps.console`.** It imports from the other apps, never the
  reverse.
- **Reads go through `queries/`.** Functions there return frozen dataclasses, never
  querysets, so a template can't trigger a query. Views don't touch the ORM.
- **No new write paths.** Action views call the functions the admin calls, such as
  `IncidentManager`. If the logic lives in a `ModelAdmin` method, lift it into the
  owning app first and make both surfaces call it.
- **Every view subclasses `ConsoleView`** (`views/base.py`). It enforces active staff
  access. An anonymous page request is redirected to the admin login. An anonymous
  htmx request gets a 204 with `HX-Redirect`, so htmx does a full redirect and never
  swaps a login form into the page.
- **One URL, two sizes.** `ConsoleView` serves `partial_template_name` only when
  `wants_partial` is true, which means an htmx request that swaps a fragment. Two htmx
  headers force the full page, because htmx swaps those responses into `<body>`:
  `HX-History-Restore-Request` and `HX-Boosted`.
- **Views don't set response headers.** `ConsoleHeadersMiddleware`
  (`apps/console/middleware.py`) owns the CSP, `Vary: HX-Request` and the never-cache
  headers for every `/console/` response. That includes a 404, a 500 and a CSRF
  refusal, which no console view returns.
- **No inline script, style or handlers** in `templates/console/`. The CSP blocks them
  and `_tests/test_templates.py` fails on them. That test scans template source only,
  so it can't see markup a helper such as `render_sparkline` emits. A view test in
  `_tests/views/test_overview.py` scans the rendered Overview page and partial for the
  same patterns. Put behaviour in `static/console/console.js`.
- **htmx is locked down** in the `htmx-config` meta in `templates/console/base.html`.
  `allowEval`, `allowScriptTags` and `includeIndicatorStyles` are off, so `hx-on` and
  bracketed trigger filters don't work. `refreshOnHistoryMiss` is on, because on a
  history cache miss htmx ignores `HX-Redirect` and would swap the empty 204 of an
  expired session into `<body>`. With the key on, a miss is a full page reload.
  `timeout` is 10 seconds, so a hung request ends in `htmx:timeout` and shows the
  banner. `historyCacheSize` is 0, so htmx keeps no page snapshots and Back is
  always a full reload. A snapshot restore replaces `<body>`, which orphans the
  `#c-banner` reference `console.js` holds, and it paints stale data with no server
  round trip. The snapshot also put page HTML with the CSRF token in `sessionStorage`,
  against the never-cache headers.
- **Polling is paused in `console.js`, not in `hx-trigger`.** A `data-poll` element
  skips a poll while the tab is hidden and while it holds an open `<details>`. Polls
  are never paused for focus or for a text selection, because a page that stops
  updating without saying so is worse than a lost selection. Every focusable element
  in a polled region needs a stable unique `id` built from its record, such as
  `incident-<pk>`, `node-<pk>` or `readiness-<key>`. After a swap htmx puts focus
  back on the element with the same `id`. An element without one loses focus on
  every poll. When a hidden tab becomes visible, `console.js` dispatches
  `console:refresh` on every `data-poll` element, so list that event in the element's
  `hx-trigger` next to the interval.
- **Colour lives in `static/console/console.css`** as tokens. Tone names match
  `config/admin_badges.py`.
- **No dead ends.** Every page links onward. The sidebar lists only pages that exist.

## No models

This app has no models, so it has no `admin.py` and no migrations. That is a
deliberate exception to the repo's app layout rule.

## Frontend dependencies

There is no npm and no build step. `static/console/vendor/` holds the only third-party
frontend code, and `VENDOR.md` there records each file's version and SHA-256.
`_tests/test_vendor.py` fails if a file changes or an unlisted file appears.

`.github/workflows/vendor-advisories.yml` reads the pinned version from `VENDOR.md` and
queries OSV for the npm package `htmx.org` every week. OSV held no advisories for that
package at any version when this was written, so the job first queries `lodash`
4.17.20 as a positive control and fails if OSV returns nothing for it. It also fails if
the htmx response is paginated. The check has only been proven to fail against another
package, never against htmx itself.

`static/console/console.js` has no automated test. Check a change to it in a browser.

To upgrade htmx, follow the steps in `static/console/vendor/VENDOR.md`. Don't add a
second vendored library without a design that names why first-party code won't do.
