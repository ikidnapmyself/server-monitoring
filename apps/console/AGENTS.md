# apps.console

The operator console at `/console/`. A consumer of the other apps: it is not a
pipeline stage and not an alert producer.

## Rules

- **Nothing imports from `apps.console`.** It imports from the other apps, never the
  reverse. Exception: `config/dashboard.py` until the auth slice deletes `/admin/`. It
  reads the Reports queries, resolves their links through `resolve_action` and gives a
  link only to a user `admits` lets in.
- **Reads go through `queries/`.** Functions there return frozen dataclasses, never
  querysets, so a template can't trigger a query. Views don't touch the ORM.
- **No new write paths.** Action views call the functions the admin calls, such as
  `IncidentManager`. If the logic lives in a `ModelAdmin` method, lift it into the
  owning app first and make both surfaces call it.
- **Every view subclasses `ConsoleView`** (`views/base.py`) and none overrides
  `dispatch`, so `_refusal` guards every route. `_tests/test_urls.py` fails otherwise.
- **The console is for active superusers only.** `admits(user)` in `access.py` is the one
  rule: `user.is_active and user.is_superuser`. `views/base.py` and the login form both
  import it from there. There are no per-model permission checks, because a superuser
  passes every one. An anonymous page request is redirected to `console:login`, which is
  `settings.LOGIN_URL`. An anonymous htmx request gets a 204 with `HX-Redirect`, so htmx
  does a full redirect and never swaps a login form into the page. A signed-in user
  `admits` refuses gets `templates/console/not_superuser.html` with status 403. It has no
  console nav, only the header and its own Sign out form. A refused htmx request gets an
  empty 403 with `HX-Refresh: true`, since htmx swaps nothing on a 403, so the browser
  reloads into the full refusal page. Non-superuser staff can still sign into `/admin/`.
- **The console signs people in and out itself.** `/console/login/` is `ConsoleLoginView`,
  Django's `LoginView` with `ConsoleLoginForm`. It admits only an active superuser. Any
  other account, an inactive one, or a wrong password gets the one `REFUSED` message, so
  the form never says which accounts exist. A signed-in user who opens it goes to the
  Overview, and a `next` that points back at the login is dropped for the same reason.
  `/console/logout/` is `LogoutView`, POST only, and lands on the login. These two are the
  only routes that aren't a `ConsoleView`, because someone the console refuses must still
  be able to sign in and out. `_tests/test_urls.py` exempts them by name. With
  `RATE_LIMIT_ENABLED=1`, `RateLimitMiddleware` allows 10 sign-in POSTs a minute per
  client to `/console/login/`, and to `/admin/login/`, which takes the same credentials.
  On the 11th it calls the view class's `rate_limited` hook, so the console answers with
  its own login page, status 429, the username kept and a `next` kept only when it's
  local. The admin login has no hook and gets the JSON 429. `manage.py createsuperuser`
  makes the first account.
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
  skips a poll while the tab is hidden, while it holds an open `<details>`, and while
  focus is inside its search form on a focused `<select>` or with an edit not yet
  submitted, since a swap resets every filter control. Moving focus out of the form lets
  polls resume, so an abandoned edit never freezes the page. Polls are otherwise never
  paused for focus or for a text selection, because a page that stops
  updating without saying so is worse than a lost selection. Every focusable element
  in a polled region needs a stable unique `id` built from its record, such as
  `incident-<pk>`, `node-<pk>` or `readiness-<key>`. A readiness key comes from
  `config.dashboard.keyed_readiness()`: the entry's key, plus the slugified lane name
  for a per-lane gap. After a swap htmx puts focus back on the element with the same
  `id`. An element without one loses focus on every poll. When a hidden tab becomes visible, `console.js` dispatches
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
- **Querystrings go through `filters.py`.** A filter object parses and validates every
  param. An invalid value falls back to the default and is named in a one-line notice.
  Only validated values reach a query or a URL, through `querystring()`.
- **A filtered list polls itself.** The partial's root is the polled element and swaps
  with `outerHTML`, so its `hx-get` always carries the current filters. Chips and pager
  links target that root with `hx-push-url="true"` and `hx-sync="#<root id>:replace"`.
  Without `hx-sync`, a late response hits a detached node and the URL and the content
  disagree.
- **The alert pages are read-only evidence.** `/console/alerts/` filters on `AlertFilter`
  (`q`, `status` firing by default, `severity`, `node`, `source`, `since`, `page`). `q`
  searches name, fingerprint and trace ID, and also matches an alert whose incident has a
  pipeline run with that trace ID. `/console/alerts/<pk>/` shows the alert, its
  incident, its history and its re-evaluation facts. No page resolves an alert. A node
  matches an alert linked by FK or an unlinked one whose `instance_id`, `instance` or
  `hostname` label names it, through `on_node` in `queries/hosts.py`, which the incident
  list shares.
- **The runs list filters on `RunFilter`** (`q`, `status` all by default, `origin`, `node`,
  `since`, `page`). `q` matches run ID, trace ID and fingerprint, and an alert fingerprint
  on the run's incident. A node matches the run's FK, or an unlinked run whose incident's
  alerts name it.
- **Run and inbox writes live in `apps/orchestration/inbox.py`.** Retry and Mark failed go
  through `RunActionView`. Drain and reclaim go through `InboxActionView`.
- **An inbox action swaps the polled root whole** (`swap="outerHTML"`) and resends the
  filter as `kept` hidden inputs, since the POST URL doesn't carry the querystring. Drain
  now executes one run in the request. Drain the oldest executes up to `DRAIN_LIMIT` (10)
  runs and starts none after `DRAIN_SECONDS` (60), through `inbox.drain_each(pks,
  seconds=…)`, so it ends well inside gunicorn's 120 second `--timeout`. Its notice says
  how many runs are left for the next drain. Never drain in a test without patching
  `PipelineOrchestrator.execute_run`.
- **Stuck has one definition**, `inbox.stuck()`: in flight (`PROCESSING`, `INGESTED`,
  `CHECKED`, `ANALYZED` or `RETRYING`), `completed_at` unset, and untouched for
  `DEFAULT_STALE_MINUTES`. The inbox lists stuck runs of every such status and marks them,
  its Stuck chip filters to them, and the dashboard counts them. Mark failed is offered,
  and `inbox.abandon` acts, only on a `PENDING` or stuck run, so it never races a live
  drain.
- **A confirm form carries a `scope_digest`.** `/console/alerts/<pk>/reevaluate/` previews
  on GET and writes nothing. The form posts the digest taken before the preview. It covers
  the node's policy and each alert's incident status as well as the alerts, and
  `apply_reeval(scope, expected=digest)` raises `StaleReeval` when any of it changed. The
  scope's rows are locked first, which serializes submits on databases that support row
  locks. SQLite doesn't, so a concurrent submit there fails with a busy `OperationalError`.
  The view answers both with a 409 and a fresh preview, so a double submit or a replay
  writes nothing. The apply form carries `data-disable-on-submit`, which `console.js` reads
  to disable its button on submit. Any later confirm page reuses this guard.
- **The node pages.** `/console/nodes/`
  filters on `NodeFilter` (`q` on hostname and instance ID, `freshness` all, silent or
  reporting) and sorts worst first by the Overview tile's rule. Its counts come from
  `open_incidents_by_node`, which follows `on_node`, so a count and the incident list it
  opens agree. A node with no hostname reads "Unnamed node" everywhere, incident headlines
  and host names included, through `node_name` in `apps/alerts/describing.py`. Its
  instance ID lives in Details. The counts, incident cards and runs on the node pages are
  intents (`view_incidents`, `view_incident`, `view_run`, `view_runs`).
- **The node policy editor is a plain form that doesn't poll.** It
  carries the `config_digest` of the stored policy, and `node_policy.save_policy` refuses a
  stale one with a 409 and fresh boxes. A busy SQLite `OperationalError` gets the same
  answer. `NodePolicyForm.save()` raises `NotImplementedError`, because a model save bumps
  `last_seen` and skips the stale check.
- **Both re-evaluate pages share `queries/reeval.py` and `partials/_reeval.html`.** A node
  preview names each alert, with its stored value and when the node reported it, and
  states the run count and the sweep before the apply. `?checker=` narrows the scope even
  when it matches nothing. Only an absent or empty one means the whole node. One that
  cleans to blank, such as a space or a NUL, narrows to nothing. A checker that is neither
  registered nor on an open alert of the node gets the fixed title "Re-evaluate one
  checker's alerts on <node>", so a typed name never reaches the page. On both pages, an
  apply whose fresh report has no changes writes nothing and answers 409 with the preview.
- **Reports hold the summary, check runs, preflight runs and analyses.** The header's
  Reports link in `base.html` opens `/console/reports/`. `partials/_reports_nav.html` shows
  the Summary, Checks, Preflight and Analyses tabs. Every page is read-only.
  - `/console/reports/` renders `build_reports(now)` from `queries/reports.py`: pipeline
    health over 24 hours, the top failure reasons over 7 days, failing checkers, provider
    usage, the latest preflight and the fleet grid. Every row links a filtered console
    list, and a reason row lands on the Runs `reason` chip worded the same way, through
    `reason_label` in `queries/runs.py`.
  - `/console/checks/` filters on `CheckFilter` (`q` on message and host, `status`, where
    failing means warning, critical or unknown, `checker`, `host`, `since`, `page`). Rows
    are worded by `describe_check`. `/console/checks/<pk>/` shows the thresholds, the raised
    alert, the run when a `PipelineRun` has that trace, and the node when exactly one has
    that hostname. Metrics, the error, the trace and the raw status sit in Details.
  - `/console/preflight/` filters on `PreflightFilter` (`status`, `since`, `page`).
    `/console/preflight/<pk>/` groups the checks under Errors, Warnings, Info, Passed and
    Other, worded by `apps/checkers/preflight_describing.py`. A node shows by name, never by
    its instance ID.
  - `/console/analyses/` filters on `AnalysisFilter` (`status`, `provider`, `since`,
    `page`). `/console/analyses/<pk>/` shows the summary, recommendations, tokens and
    duration. The stored `provider_config` renders only inside Details.
  - The Runs list's `reason` filter (a `REASONS` code, or `other`) shows as a removable
    chip.
- **System holds routes, channels, providers, API keys and users.** The header's System
  link in `base.html` opens `/console/routes/`. `partials/_system_nav.html` shows the
  Routes, Channels, Providers, API keys and Users tabs in that order. The admins for the
  first four are stock and read-only.
- **The route pages.** `/console/routes/` lists every route in match order through
  `build_route_list`, which reads `config.netmap.get_map_context()`, so each card says
  when a route is off, never matches or is never reached, and whether it can deliver.
  `/console/routes/<pk>/` and `/console/routes/new/` render `RouteForm`. Conditions are
  rows, and "Add condition" (`add_condition`) re-renders and saves nothing. A save posts
  the `version` it opened and goes through `save_route`, which refuses a stale one with a
  409 and the fresh route. A stored match the rows can't reproduce (`representable`) is
  shown as stored and kept unless "Replace them" is ticked. A save lands on the routes list
  at the route's card. `/console/routes/<pk>/delete/` confirms first and states where the
  traffic goes, from `routing.fallthrough`.
- **The channel pages.** `/console/channels/` lists channels with the routes on each,
  and `q` searches names. `/console/channels/new/` picks a driver first, then
  `?driver=<key>` renders that driver's form. `/console/channels/<pk>/delete/` confirms
  first and names the routes that stop delivering, from `lanes_cut_by_delete`.
- **A channel page never renders a secret.** `/console/channels/<pk>/` renders
  `ChannelForm`, whose secret inputs render empty and whose undeclared settings show by
  name only, in Details. The page posts to its own URL with `?driver=`, the driver its
  fields were built for. "Show this driver's settings" (`switch_driver`) re-renders and
  saves nothing, and a driver key that's neither registered nor the channel's own is a 400.
  A save posts `digest`, the `channel_digest` of the row it opened, and `save_channel`
  refuses a stale one with a 409. A save lands on the channel page.
- **The provider pages mirror the channel pages.** `/console/providers/` lists the
  provider in use first, and its header says which one analysis uses, or that the built-in
  local analyzer does. `/console/providers/new/` picks a kind from
  `IntelligenceProvider.PROVIDER_CHOICES`, then `?provider=<key>` renders `ProviderForm`.
  `local` is the fallback, never a row, so it isn't offered. Secrets render empty, "Show
  this provider's settings" (`switch_provider`) saves nothing, an unknown kind is a 400,
  and `save_provider` refuses a stale `digest` with a 409. When another provider is on, a
  changer sees "Turning this on turns off <name>." under Active. `/console/providers/<pk>/delete/`
  says what analysis falls back to.
- **An API key's token is shown once.** `/console/keys/` lists keys by name with the
  prefix, the endpoints and the last use. `/console/keys/new/` validates `APIKeyForm` and
  renders `key_created.html` with the raw token, status 200 and `Cache-Control: no-store`.
  The token never goes to `messages`, and re-posting the form makes a second key. The
  editor saves through `save_key` with a 409 when stale. `/console/keys/<pk>/delete/` says
  that anything still sending the key gets refused, and when it was last used.
- **The user pages.** `/console/users/` lists every user by username with their name,
  email, last sign-in and a label: Active, Inactive, or "Not a superuser" for an active
  account the console refuses, which also gets a Make superuser button. Groups have no
  page. `/console/users/new/` makes an active superuser, always, with a password
  `AUTH_PASSWORD_VALIDATORS` accepts. `/console/users/<pk>/` edits username, email, names
  and Active, and a successful save redirects back to the page with a message.
  `/console/users/<pk>/password/` sets a password, and setting your own keeps you signed
  in through `update_session_auth_hash`. `set_password` writes only the password, onto the
  locked row, so a concurrent edit survives and a deleted user stays deleted.
  `/console/users/<pk>/make-superuser/` is POST only, and `make_superuser` refuses an
  inactive user or one already a superuser with a warning and no write.
  `/console/users/<pk>/delete/` confirms first and says when the user last signed in.
- **Two guards keep the console reachable.** `removal_refusal(actor, user, verb)` in
  `queries/users.py` is the one rule for deactivating and deleting, shared by
  `update_user` and `delete_user`. It checks the last active superuser first, then self:
  removing the last active superuser reads "At least one active superuser must remain.",
  and otherwise removing yourself reads "You can't deactivate your own account." or "You
  can't delete your own account.". The order matters, because the actor is always an
  active superuser, so checking self first would hide the last-superuser rule. It locks
  the other active superusers with `select_for_update()`, so the caller holds the lock
  until its write commits and two removals can't both pass. Only that locked call, inside
  the writing transaction, decides. The user page and the delete page call it with
  `lock=False` to show or hide a button, take no lock and open no transaction, so their
  answer is advisory. A refused edit saves nothing and re-renders the form as the operator
  submitted it, with the reason as a `Notice` and status 409, so their other changes
  aren't lost. A save writes only the form's fields onto the locked row, so a password set
  or a promotion that landed meanwhile survives. A busy database gets the same re-render
  with its own notice. A refused delete shows the reason and no button on GET, and on POST
  gives a warning message and a 303 to the user's page. A busy or locked database on
  delete gets the same 303 with a warning that nothing was deleted.
- **Fleet policy** (`/console/policy/`) renders `build_policy_overview`. Its edit and
  re-evaluate links are intents.
- **A missing record gets a console 404 page** with a link onward, returned with status
  404, as `IncidentDetailView` does. Don't raise a bare `Http404` from a page view.
- **Stage output is untrusted in shape.** Every page reads stage output through
  `queries/stages.py`, which turns an `output_snapshot` into `CheckBody`, `AnalyzeBody`,
  `NotifyBody` or `RawBody`. Every
  builder must survive a list, a string, `None` and wrong-typed members without
  raising, because a raise is a 500 on every poll. Raw JSON renders through
  autoescaping only, inside an entry's Details.
- **The incident page decodes nothing.** Every headline, stage outcome, check line,
  analysis and delivery comes from the owning app's describer, and the read model builds
  no admin URL. Navigation is an `Action` intent (`view_run`, `view_alert`), added only
  when the description has no action of its own, so a link never hides a fix.
- **`intents.py` is the only place that builds an admin URL.** `INTENTS` maps each
  `Action.intent` to a URL builder and a method. `resolve_action(action)` turns an
  `Action` into a link, a POST button, or plain text when the intent is unknown or the URL
  can't be built. Every intent now opens a console page. Reports have `view_checks`,
  `view_check_run`, `view_preflight`, `view_preflight_run`, `view_analyses` and
  `view_analysis`. Routes, channels, providers and keys have theirs: `view_route`,
  `edit_route`, `add_route`, `delete_route`, `view_channel`, `edit_channel`,
  `add_channel`, `delete_channel`, `view_provider`, `edit_provider`, `add_provider`,
  `delete_provider`, `view_key`, `edit_key`, `add_key`, `delete_key`, `view_user`,
  `edit_user`, `add_user`, `set_user_password`, `delete_user` and `make_superuser`. A
  `view_*` intent opens the page, or the list when it has no `pk`. A name link uses it. An
  `edit_*` intent opens the same page, and a remedy such as "Give it an active channel"
  uses it. `admin_url()` covers only the header's Admin link, until 12b removes `/admin/`.
  No template or query reverses an `admin:` name or writes `/admin/`, and
  `_tests/test_intents.py` scans for both. Templates resolve an action with
  `{% load console %}{% resolved action as act %}` and render it through
  `partials/_action.html`.
- **A `Description` renders through `partials/_description.html`.** It prints the
  headline with its tone icon, the detail, the resolved action and the facts. Pass a
  stable `id`, because the action and the Details derive theirs from it. Don't lay out a
  description by hand in another template.
- **Identifiers live in Details.** `partials/_details.html` is a `<details data-details>`
  that holds the facts, copy buttons and raw JSON. Run ids, trace ids, error text and
  codes go there as facts, never in a headline or a detail.
  `_tests/test_guard.py` renders every page against `build_world()` from
  `_tests/fixtures.py`, a world with every routing gap and failure kind, and fails if any
  text outside `data-details` holds a UUID, a `snake_case` token, a `{` or a reason code.
  A new outcome or reason code needs a case in `build_world()`. A stored policy value
  renders as stored, like text a source sent. The editor validates every new value, so
  only legacy rows hold odd ones.
- **The incident timeline is capped.** The page shows lifecycle entries plus the newest
  `TIMELINE_LIMIT` others, and `?all=1` shows everything. Bodies are built only for
  entries that survive the cut. The database read is not capped.
- **A disclosure needs two ids.** htmx restores focus by the active element's own id,
  and for a `<details>` that element is the `<summary>`. Give the `<summary>` its own
  stable id.
- **Incident actions are one POST-only view.** `IncidentActionView`
  (`views/incident_action.py`) serves `incidents/<pk>/<action>/` and calls
  `IncidentManager.transition`, which owns the status rules and locks the row. The view
  holds no rule of its own. The admin's own incident buttons sit behind `is_staff` alone,
  so the console, which admits superusers only, is the stricter surface.
  `IncidentPageView` (`views/incident.py`) is the one place that builds
  the page context, so the page and the action response can't drift.
- **Rerunning checks is its own POST-only view.** `IncidentRerunView`
  (`views/incident_rerun.py`) serves `incidents/<pk>/rerun/` and calls
  `apps.alerts.check_integration.rerun_incident_checks`, which owns the rules. It runs the
  incident's checkers only when the incident's node is this machine and names a checker
  this machine has, and otherwise refuses with a warning notice. The view catches only
  `RerunRefused` as a refusal. A `PipelineRunFailed` means the enqueue failed and rolled the
  recording back, so its warning says nothing was recorded. The button appears only
  when `can_rerun(incident)` holds. The rerun records alerts and enqueues runs without
  draining them, so they notify real channels once `process_inbox` drains. Never drain
  the inbox in a test.
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
  `available_incident_actions`, and the rerun when `can_rerun(incident)` holds. A closed
  incident with nothing to rerun gets no buttons and no "Do this" box. A button can still
  be stale by the time it's clicked. `transition` then refuses it, and the operator sees a
  warning.
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
