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
  and `_tests/test_templates.py` fails on them. Put behaviour in
  `static/console/console.js`.
- **htmx is locked down** in the `htmx-config` meta in `templates/console/base.html`.
  `allowEval`, `allowScriptTags` and `includeIndicatorStyles` are off, so `hx-on` and
  bracketed trigger filters don't work. `refreshOnHistoryMiss` is on, because on a
  history cache miss htmx ignores `HX-Redirect` and would swap the empty 204 of an
  expired session into `<body>`. With the key on, a miss is a full page reload.
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
package at any version when this was written. The check has only been proven to fail
against another package, never against htmx itself.

`static/console/console.js` has no automated test. Check a change to it in a browser.

To upgrade htmx, follow the steps in `static/console/vendor/VENDOR.md`. Don't add a
second vendored library without a design that names why first-party code won't do.
