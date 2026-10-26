---
title: "Sysadmin Console PR 11: Break-glass Design"
parent: Plans
---

# Sysadmin console PR 11: break-glass

Slice 11 in `2026-10-15-sysadmin-console-foundation-design.md`. The console becomes the only
operator surface. `/admin/` becomes stock Django for superusers, with secrets hidden. The admin
dashboard, its skin and its helpers are deleted.

## What exists

- Every app admin is stock and read-only. None has actions, inlines, forms or custom views.
- `config/admin.py` holds `SECTION_MAP`, `MonitoringAdminSite` (dashboard `index()`, regrouping
  `get_app_list()`) and `APIKeyAdmin`. `MonitoringAdminConfig` installs it as the default site.
- `config/dashboard.py` holds three things:
  - the dashboard context
  - the readiness code that the console Overview uses through `keyed_readiness`
  - the fleet metrics that `apps/console/queries/reports.py` uses
- `templates/admin/dashboard.html`, `templates/admin/base_site.html` and
  `static/admin/css/ops.css` skin the admin.
- `config/admin_links.py` has no production caller. `config/admin_badges.py` holds the tone
  tables that about 15 non-admin modules use, plus `badge()` and `tinted()`, which only
  `ops.css` styles.
- The console has no login of its own. `apps/console/views/base.py` redirects to
  `admin:login`, and the header posts logout to `admin:logout`. No `LOGIN_URL` is set.
- `/` already redirects to `console:overview`.

## Console login

- `console:login` and `console:logout` use Django's `LoginView` and `LogoutView` with a Primer
  template.
- The login form turns away inactive and non-staff accounts with a plain message.
- `LOGIN_URL = "console:login"` and `LOGIN_REDIRECT_URL = "console:overview"`.
- Signed-out console requests redirect there, and an htmx request gets an `HX-Redirect` there.
- The header's Admin link shows only to superusers. Logout posts to the console.

## Break-glass admin

- A small `AdminSite` subclass stays. Its `has_permission` admits only an active superuser.
- Each model is registered with a plain, editable `ModelAdmin`.
- Secret fields stay excluded, so only the console changes them:
  - `NotificationChannel.config`
  - `IntelligenceProvider.config`
  - `AnalysisRun.provider_config`
  - `APIKey.key`
- Users and groups stay stock.
- An admin edit writes the raw row. It skips the console's version checks and incident
  transitions, and it enqueues no run. The docs say so.

## Delete

- `SECTION_MAP`, the `index()` and `get_app_list()` overrides, `get_dashboard_context` and
  `_row`.
- `templates/admin/dashboard.html`, `templates/admin/base_site.html` and
  `static/admin/css/ops.css`.
- `config/admin_links.py`, `badge()` and `tinted()`.
- Their tests.

## Move

- Readiness goes to `config/readiness.py`: `build_readiness`, `keyed_readiness`, their helpers
  and `NODE_RECENT_MINUTES`. `apps.alerts` reads `NODE_RECENT_MINUTES` too, so this stays in
  `config`.
- Fleet metrics go to `apps/console/queries/fleet.py`: `METRIC_UNITS`, the metric dataclasses and
  `build_fleet_metrics`. Nothing in `config` imports `apps.console` any more, so the PR 10
  exception is removed.
- `config/admin_badges.py` becomes `config/tones.py`.
- Tests move with their code.

## Wording and docs

- The "Django Admin" hints name console pages instead. They're in `setup_cluster`,
  `apps/checkers/checks.py` and `apps/checkers/preflight/checks.py`.
- AGENTS.md convention 2 drops the "substantive admin.py" requirement.
- Convention 3 is rewritten. The console is the operations surface, and `/admin/` is superuser
  break-glass.
- The admin sections of each app's AGENTS.md and README are updated to match.
- So are `docs/Architecture.md`, `docs/Security.md`, `docs/Deployment.md` and
  `docs/Setup-Guide.md`.

## Tests

- Console tests that assert `admin:login` assert `console:login`.
- Login takes staff, and turns away non-staff and inactive accounts. Logout signs out.
- `/admin/` turns away staff who aren't superusers, and lets a superuser edit.
- No secret field's stored value appears on any admin page.
- The console guard covers the login page.
- 100% branch coverage on changed code.

## Out of scope

- User and group management in the console.
- An audit trail for console writes. That comes next.
- Renaming `apps/checkers/admin_charts.py`.
