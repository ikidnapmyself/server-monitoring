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
- **The look is GitHub Primer, written by hand.** `static/console/console.css` is one
  file, grouped by component. Its custom properties carry Primer's functional token
  names (`--fgColor-*`, `--bgColor-*`, `--borderColor-*`) with Primer's light and dark
  values. Components are `c-` classes shaped after Primer: `c-box`, `c-label`, `c-state`,
  `c-counter`, `c-button`, `c-seg`, `c-flash`, `c-blankslate`, `c-pagination`,
  `c-timeline` and the issue-page `c-pagehead` and `c-sidebar`. Reuse one before writing
  a new rule. Colour is only ever a token: no hex outside the two token blocks.
- **Tones come from `config/admin_badges.py`.** A `c-tone-<tone>` class sets the text
  colour and the `--tone-*` properties a component reads, so one tone class works on a
  label, a flash or a timeline badge. Incident states use `c-status--<status>` instead,
  because GitHub's colours for state differ from the tone table: open is green,
  acknowledged attention, resolved and closed done purple. `_tests/test_css.py` checks
  every text pair at AA in both themes and needs a rule per tone.
- **Icons are Octicons** from the sprite `static/console/vendor/octicons.svg`. Render one
  with `{% include "console/partials/_octicon.html" with name="..." %}`, never an inline
  path. A name the sprite lacks fails `_tests/test_templates.py`.
- **No dead ends.** Every page links onward. The underline nav under the header lists
  only pages that exist.
- **Reads need active staff, nothing more.** The console doesn't check the admin's
  model permissions for reads. A staff user without `orchestration.view_stageexecution`
  gets a 403 on the admin's stage page and still sees the same snapshot on the incident
  page. That is the approved design. Action views check the permissions the admin
  checks.
- **Querystrings go through `filters.py`.** A filter object parses and validates every
  param. An invalid value falls back to the default and is named in a one-line notice.
  Only validated values reach a query or a URL, through `querystring()`.
- **A filtered list polls itself.** The partial's root is the polled element and swaps
  with `outerHTML`, so its `hx-get` always carries the current filters. Chips and pager
  links target that root with `hx-push-url="true"` and `hx-sync="#<root id>:replace"`.
  Without `hx-sync`, a late response hits a detached node and the URL and the content
  disagree.
- **A missing record gets a console 404 page** with a link onward, returned with status
  404, as `IncidentDetailView` does. Don't raise a bare `Http404` from a page view.
- **Stage output is untrusted in shape.** `queries/incident.py` turns an
  `output_snapshot` into `CheckBody`, `AnalyzeBody`, `NotifyBody` or `RawBody`. Every
  builder must survive a list, a string, `None` and wrong-typed members without
  raising, because a raise is a 500 on every poll. Raw JSON renders through
  autoescaping only, inside an entry's Details.
- **The incident page decodes nothing.** Every headline, stage outcome, check line,
  analysis and delivery comes from the owning app's describer, and the read model builds
  no admin URL. Navigation is an `Action` intent (`view_run`, `view_alert`), added only
  when the description has no action of its own, so a link never hides a fix.
- **The incident timeline is capped.** The page shows lifecycle entries plus the newest
  `TIMELINE_LIMIT` others, and `?all=1` shows everything. Bodies are built only for
  entries that survive the cut. The database read is not capped.
- **A disclosure needs two ids.** htmx restores focus by the active element's own id,
  and for a `<details>` that element is the `<summary>`. Give the `<summary>` its own
  stable id.
- **Incident actions are one POST-only view.** `IncidentActionView`
  (`views/incident_action.py`) serves `incidents/<pk>/<action>/` and calls
  `IncidentManager.transition`, which owns the status rules and locks the row. The view
  holds no rule of its own. It checks active staff first, then `alerts.change_incident`.
  The admin's own incident buttons sit behind `is_staff` alone, so the console is the
  stricter surface. `IncidentPageView` (`views/incident.py`) is the one place that builds
  the page context, so the page and the action response can't drift.
- **An action's outcome is a `Notice`, not a Django message.** An htmx POST gets the
  incident partial back with status 200 whether the action was applied or refused, with
  the outcome passed in the context as `notice`. The messages framework is shared with
  the admin, and a polled partial that rendered `messages` ate the admin's and leaked its
  own. Only a plain POST uses it: it gets a 303 to the incident page, and
  `templates/console/incident.html` renders `messages` outside the polled wrapper, so only
  a full page load consumes them. A poll never carries a notice.
- **The notice is marked `data-fresh`.** `console.js` focuses it after the swap, because
  the clicked button is gone, and skips the one poll that finds it still fresh. That poll
  is usually one that queued behind the POST and would replace the outcome the moment it
  landed.
- **A failed action says so and reloads.** htmx swaps nothing on a 4xx, a 5xx, a network
  error or a timeout. For a non-GET request `console.js` shows the banner's
  `data-action-text` and dispatches `console:refresh` on the polled element around the
  target. A repeat is safe because `transition` refuses an action already applied. The
  banner survives the reload it asked for and clears on the next successful load after it.
  The reload obeys the usual pauses, so an open note holds it off and keeps what was typed.
- **A non-GET request can't be the login's `next`.** `ConsoleView._refusal` sends an
  anonymous GET or HEAD back to its own path. Anything else goes to `landing_path()`,
  which defaults to the Overview. An action view overrides it with the page it acts on.
- **Buttons come from the read model.** `IncidentDetail.do` holds the transitions from
  `available_incident_actions` only when the view passes `can_act=True`, and the rerun
  only when it passes `can_rerun=True` and `can_rerun(incident)` holds. A user without
  the permission gets no buttons and no "Do this" box. A button can still be stale by the
  time it's clicked. `transition` then refuses it, and the operator sees a warning.
  Every action form disables every `.c-button` in `#incident` while its request runs. A
  second click would otherwise abort the first request in the browser only, and the
  server would run both.
- **A transition announces.** It enqueues a `MANUAL` run that notifies real channels once
  `process_inbox` drains. Never click an action button against a database with live
  channels to try something out, and never drain the inbox in a test.
- **A form in a polled region carries its own `{% csrf_token %}`.** Each poll renders a
  freshly masked token for the same secret, so any of them stays valid until the secret
  rotates at login. The form repeats `?all=1` as a hidden `all` field, because the POST
  URL doesn't carry the page's querystring. Text an operator types lives in a `<details>`,
  since an open one pauses the poll that would otherwise wipe it. The note's cap is
  `IncidentPageView.summary_limit`, which the view enforces and the template prints as
  `maxlength`.
- **Printing.** `console.js` opens the top-level `<details>` under `[data-print-open]`
  on `beforeprint` and closes the ones it opened on `afterprint`. Raw JSON stays closed
  and hidden in print.

## No models

This app has no models, so it has no `admin.py` and no migrations. That is a
deliberate exception to the repo's app layout rule.

## Frontend dependencies

There is no npm and no build step. `static/console/vendor/` holds the only third-party
frontend files: htmx, and the Octicons sprite. `VENDOR.md` there records each file's
version, source and SHA-256. `_tests/test_vendor.py` fails if a file changes or an
unlisted file appears, and if the sprite holds anything but `<svg>`, `<symbol>` and
`<path>`. The sprite is data with no runtime behaviour. To add an icon, follow
`VENDOR.md`.

`.github/workflows/vendor-advisories.yml` reads the pinned version from `VENDOR.md` and
queries OSV for the npm package `htmx.org` every week. OSV held no advisories for that
package at any version when this was written, so the job first queries `lodash`
4.17.20 as a positive control and fails if OSV returns nothing for it. It also fails if
the htmx response is paginated. The check has only been proven to fail against another
package, never against htmx itself.

`static/console/console.js` has no automated test. Check a change to it in a browser.

To upgrade htmx, follow the steps in `static/console/vendor/VENDOR.md`. Don't add
another vendored library without a design that names why first-party code won't do.
