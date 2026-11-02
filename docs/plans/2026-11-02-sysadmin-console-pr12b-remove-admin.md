---
title: "Sysadmin Console PR 12b: Remove /admin/"
parent: Plans
---

# Remove /admin/ implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `/admin/` is gone and `django.contrib.admin` is uninstalled. The console is the only UI.

**Architecture:** First move everything the console still borrows from admin modules (readiness,
fleet metrics, tone tables, the sparkline) into neutral homes, porting the moves already written
on the closed #268 branch. Then delete the admin, its URLs, templates, CSS, tests and every
link to it. The `admin_logentry` table stays, unused.

**Tech Stack:** Django 5.2, pytest-django.

Design: `docs/plans/2026-10-29-sysadmin-console-auth-design.md`, section "12b: remove /admin/".
Merge order: this PR merges after #272 (trusted-proxy client IP). Rebase onto main once #272
lands. Expect small conflicts in `config/middleware/rate_limit.py` and `docs/Security.md`.

Rules for every task:

- Run the named tests after each code step. pytest's addopts already sets `-q`.
- Commit with `git commit` so pre-commit runs. Messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Docs-only commits may use
  `--no-verify`.
- Stage files by name. Never stage `.agent-tree/` or
  `docs/plans/2026-09-14-refire-staleness-open-question.md`. Never push.
- No em or en dashes in code, docs or messages. Never use gate, seam, wire, leverage, crucial,
  navigate or align. Minimal comments.
- Don't edit any file under `docs/plans/` other than this one.

---

### Task 1: Port the readiness, fleet and tones moves

The closed #268 branch (`origin/feat/console-break-glass`, or the commits by SHA) has three
pure moves. Port them in order:

- `11f76fd` readiness: `config/dashboard.py` readiness code moves to `config/readiness.py`,
  `config/_tests/test_dashboard_readiness.py` becomes `config/_tests/test_readiness.py`.
- `cb7e67a` fleet: `build_fleet_metrics` and `METRIC_UNITS` move to
  `apps/console/queries/fleet.py` with `apps/console/_tests/queries/test_fleet.py`.
- `b01c1b8` tones: `config/admin_badges.py` becomes `config/tones.py`, its tests
  `config/_tests/test_tones.py`.

**Step 1:** `git cherry-pick 11f76fd cb7e67a b01c1b8`, one at a time. Main has moved since,
so resolve conflicts by keeping main's behavior and applying only the move. If a pick is
messier than its conflict markers suggest, redo that move by hand: same destination module,
same names, every importer updated.

**Step 2:** Find stragglers the old commits didn't know about:

```bash
git grep -n "config.admin_badges\|config.dashboard import\|from config import dashboard" -- '*.py'
```

Expected: only `config/admin.py`, `config/dashboard.py` itself and their own tests (all deleted
in Task 3). Point everything else at `config.tones`, `config.readiness` or
`apps.console.queries.fleet`. `NODE_RECENT_MINUTES` lives wherever 11f76fd put it.

**Step 3:** Rename `apps/checkers/admin_charts.py` to `apps/checkers/charts.py` and its test to
`apps/checkers/_tests/test_charts.py` with `git mv`. Update `apps/alerts/node_overview.py` and
any other importer.

**Step 4:** `uv run pytest apps config`. Expected: all pass.

**Step 5:** Commit each pick as it lands (keep the original subjects), then the rename as
`refactor(checkers): the sparkline lives in charts.py`.

---

### Task 2: Hints name console pages

Port `b796707`, only its changes to `apps/checkers/checks.py`,
`apps/checkers/preflight/checks.py`, `config/management/commands/setup_cluster.py` and their
tests (`apps/checkers/_tests/test_checks.py`, `apps/checkers/_tests/preflight/test_checks.py`,
`config/_tests/commands/test_setup_cluster.py`). Skip its `login.py` and `config/apps.py` parts.

Every hint that says "Django Admin" or "Django admin" names the console page instead:
channels at `/console/channels/` and `/console/channels/new/`, routes at `/console/routes/`
and `/console/routes/new/`. Check the URLs exist in `apps/console/urls.py`. Also fix the
`--help` text in `setup_cluster.py` that says "email: add via admin" (channels are added in
the console) and the comment at its line ~308 that mentions admin forms.

```bash
git grep -ni "django admin\|via admin" -- '*.py'
```

Expected after: no output outside files Task 3 deletes.

Run: `uv run pytest apps/checkers config/_tests/commands`. Commit:
`fix: hints send operators to console pages, not the Django admin`.

---

### Task 3: Uninstall the admin

**Step 1: Write the failing tests** in `config/_tests/test_no_admin.py`:

- `client.get("/admin/")` and `client.get("/admin/login/")` are 404.
- `"django.contrib.admin" not in settings.INSTALLED_APPS` and no entry is an `AdminConfig`
  subclass (check `django.apps.apps.get_app_configs()` labels: no `admin`).
- No tracked Python file under `apps/` or `config/` imports `django.contrib.admin` or
  `from django.contrib import admin`. Walk the files with `pathlib`, skip `migrations/`.
- `/admin/` isn't in `config.middleware.constants.EXEMPT_PATH_PREFIXES`, and no rate-limit
  table (`settings.RATE_LIMITS`, `rate_limit.DEFAULT_RATE_LIMITS`) has an `/admin/` key.

Run: `uv run pytest config/_tests/test_no_admin.py`. Expected: fail.

**Step 2: Delete**

- `config/settings.py`: drop `"config.apps.MonitoringAdminConfig"` from `INSTALLED_APPS`, and
  `/admin/login/` from `RATE_LIMITS`.
- `config/apps.py`: delete `MonitoringAdminConfig`. `ConfigAppConfig.ready` only imports
  `config.checks`. The module docstring says it's the config app's configuration.
- `config/urls.py`: drop the admin import and `path("admin/", ...)`.
- `config/middleware/constants.py`: `EXEMPT_PATH_PREFIXES = ("/static/",)`.
- `config/middleware/rate_limit.py`: drop `/admin/login/` from `DEFAULT_RATE_LIMITS`, and
  `COUNTED_DESPITE_EXEMPTION` with its comment and every use. Simplify the code that read it.
- Delete: `config/admin.py`, `config/admin_links.py`, `config/dashboard.py` (whatever is left
  after Task 1), `apps/alerts/admin.py`, `apps/checkers/admin.py`, `apps/intelligence/admin.py`,
  `apps/notify/admin.py`, `apps/orchestration/admin.py`, `templates/admin/`,
  `static/admin/css/ops.css`.
- Delete their tests: `config/_tests/test_admin.py`, `test_admin_links.py`,
  `test_admin_site.py`, `test_api_key_admin.py`, `test_dashboard.py`,
  `test_dashboard_render.py`, `test_ops_css.py`, and each `apps/*/_tests/test_admin.py`.
  Before deleting a test file, read it. If it asserts behavior that lives outside the admin
  (a model method, a query, a describer), move that test next to the code it tests.
- `config/_tests/test_no_inline_colour.py`: if it only scans admin templates or `ops.css`,
  delete it. If it also covers console files, drop the admin paths.
- `config/_tests/test_settings.py`: drop the `ops.css` static assertion and the
  `/admin/login/` limit assertion.
- `apps/console/intents.py`: delete `admin_url`, its template tag in
  `apps/console/templatetags/console.py` if one exists, and their tests.
- `templates/console/base.html`: delete the header's Admin link. Update
  `apps/console/_tests` assertions on the header.
- Any console test or code still reversing `admin:*`:

```bash
git grep -n "admin:\|admin_url\|/admin/" -- '*.py' '*.html' ':!docs'
```

Expected: no output except `test_no_admin.py`.

- `apps/orchestration/migrations/0020_*`: leave it. Migrations are history.

**Step 3: Run**

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run pytest
```

Expected: check is clean, no new migrations, all pass.

**Step 4: Commit**

`feat!: remove /admin/ and uninstall django.contrib.admin`

---

### Task 4: Docs

**Files:** `AGENTS.md`, `.github/copilot-instructions.md`, every `apps/*/AGENTS.md`, every
`apps/*/README.md` that mentions the admin, `docs/Security.md`, `docs/Architecture.md`,
`docs/Deployment.md`, `docs/Setup-Guide.md`.

- `AGENTS.md`: convention 2 drops "a substantive `admin.py`" from the required app layout,
  and the "App structure" list drops `admin.py`. Convention 3 becomes: the console at
  `/console/` is the only UI, superusers only. Drop the `MonitoringAdminSite`, dashboard,
  `SECTION_MAP` text. Readiness lives in `config/readiness.py` (`keyed_readiness()`), tones
  in `config/tones.py`. Convention 14 points at `config/readiness.py`, not
  `config/dashboard.py`. The `console` row in the core apps table drops the incident-admin
  sentence. The App-local rules line drops "admin conventions".
- Per-app AGENTS.md and README.md: delete admin sections. Where they tell an operator to do
  something in the admin, name the console page.
- `docs/Security.md`: drop `/admin/` from exempt paths and the sign-in pages section (only
  `/console/login/` remains). Delete text about the admin site.
- `docs/Architecture.md`, `docs/Deployment.md`, `docs/Setup-Guide.md`: the console is the UI.
  `manage.py createsuperuser` makes the first account.

```bash
git grep -ni "django admin\|/admin/\|admin\.py\|MonitoringAdmin\|admin site\|ops\.css\|config/dashboard" -- '*.md' ':!docs/plans'
```

Expected: no output, or only lines that say the admin was removed.

Voice check:

```bash
git diff origin/main -- '*.md' ':!docs/plans' | grep -nP "^\+.*(\x{2014}|\x{2013}|\bgate\b|\bseam\b|\bwire\b|leverage|crucial|navigate|\balign)"
```

Expected: no output. Commit: `docs: the console is the only UI`.

---

### Task 5: Verify

1. `uv run pytest`. All pass.
2. `uv run coverage run -m pytest && uv run coverage report --include="config/*,apps/*" --skip-covered --show-missing`.
   Every changed or moved line is covered.
3. `uv run ruff check . && uv run black . --check && uv run mypy .`. Clean.
4. `uv run python manage.py collectstatic --noinput --dry-run`. No error.
5. Run the server, sign in at `/console/login/`, screenshot the overview header (no Admin
   link) and confirm `/admin/` is a 404.
