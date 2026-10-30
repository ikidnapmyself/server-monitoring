---
title: "Sysadmin Console PR 12: Auth Design"
parent: Plans
---

# Sysadmin console PR 12: auth

The last slice of the migration, after `2026-10-27-sysadmin-console-superuser-only-design.md`.
The console gets its own login and user management, and then `/admin/` is removed. The user
split it into two PRs:

- **12a.** Console login and logout, plus users.
- **12b.** Remove `/admin/` and `django.contrib.admin`.

Decisions the user made:

- Every user created in the console is an active superuser.
- Groups get no page. Their tables stay.
- `django.contrib.admin` is uninstalled entirely. The `admin_logentry` table stays, unused. The
  audit trail that follows gets its own model.

## What exists

- The console admits only active superusers, through `admits(user)` in
  `apps/console/views/base.py`. Signed-out requests redirect to `admin:login`, and htmx gets
  `HX-Redirect`. The header posts Sign out to `admin:logout`.
- No model has a foreign key to the user.
- The closed #268 branch, `feat/console-break-glass`, holds a console login (`fedb8bf`,
  `7b3284b`), its rate limit (part of `31ecb08`), the readiness, fleet and tones moves
  (`11f76fd`, `cb7e67a`, `b01c1b8`) and the hint rewording (`b796707`). That login admitted
  staff. Here it admits superusers.

## 12a: login

- `console:login` and `console:logout` use Django's `LoginView` and `LogoutView` with a Primer
  template. Logout is POST only and lands on the login page.
- The login form admits only an active superuser. Any other account gets the same message as a
  wrong password, so the form doesn't reveal which accounts exist.
- `LOGIN_URL = "console:login"` and `LOGIN_REDIRECT_URL = "console:overview"`. `ConsoleView`
  redirects there, and the header's Sign out posts to `console:logout`.
- A signed-in superuser who opens the login page goes to the overview.
- `RateLimitMiddleware` limits `/console/login/` to 10 attempts a minute when
  `RATE_LIMIT_ENABLED=1`. The login view renders the refusal itself, with status 429, and
  keeps the username.
- The refusal page's Sign out posts to `console:logout`.

## 12a: users

The page is `console:users`, a tab under System after API keys.

- **List.** Shows username, name, email, last sign-in and an Active or Inactive label. A
  non-superuser row is marked "Can't sign in" and offers "Make superuser".
- **Create.** Fields are username, email, first and last name, password and confirmation. The
  password goes through `AUTH_PASSWORD_VALIDATORS`. The account is saved with `is_superuser`,
  `is_staff` and `is_active` all set.
- **Edit.** Same fields without the password, plus Active.
- **Set password.** Its own form: new password and confirmation, validated. Changing your own
  password keeps you signed in, through `update_session_auth_hash`.
- **Make superuser.** A POST that sets `is_superuser` and `is_staff`.
- **Delete.** A confirm page, the same pattern as route delete.
- **Guards**, in the user query layer so every view shares them:
  - you can't deactivate or delete yourself
  - the last active superuser can't be deactivated or deleted
- Each refusal is a `Notice`, worded for the operator.
- Describers follow convention 14. No user page shows a raw id outside `data-details`.

## 12b: remove /admin/

- Before `/admin/` goes, a small PR makes the rate limiter read the client IP from
  `X-Forwarded-For` only when the request comes from a configured trusted proxy. Until
  then, behind a proxy every client shares one `/console/login/` bucket and one
  `/admin/login/` bucket, so anyone's bad attempts lock every operator out for the minute.
- Uninstall `django.contrib.admin`. Delete `MonitoringAdminConfig`, the `admin/` URLs,
  `config/admin.py`, every `apps/*/admin.py`, `config/admin_links.py`, `config/dashboard.py`,
  `templates/admin/` and `static/admin/css/ops.css`.
- Port the readiness, fleet and tones moves from #268's branch.
- Rename `apps/checkers/admin_charts.py` to `apps/checkers/charts.py`.
- Delete the `admin_url` intent, the header's Admin link and `/admin/` in
  `config/middleware/constants.py`.
- The "Django admin" hints in `setup_cluster` and the checker hints name console pages.
- `manage.py createsuperuser` stays the way to make the first account.
- Docs: AGENTS.md convention 2 drops `admin.py` from the required app layout. Convention 3 says
  the console is the only UI. The per-app AGENTS.md, Security.md, Architecture.md,
  Deployment.md and Setup-Guide.md change to match.
- Tests: `/admin/` is a 404, nothing imports `django.contrib.admin`, and the moved code keeps
  its tests.

## Tests (12a)

- Login: a superuser signs in and lands on the overview. Staff, plain, inactive and wrong
  password attempts all get the same refusal. The 11th attempt in a minute gets a 429 with the
  username kept. Logout signs out and refuses GET.
- Signed-out console requests redirect to `console:login`, and htmx gets `HX-Redirect` there.
- Users: list, create, edit, set password (own and another's), make superuser, delete. Both
  guards are tested on every path that could trip them.
- The guard test covers the login page and every user page.
- 100% branch coverage on changed code.

## Out of scope

- Groups and per-user permissions.
- Email password reset.
- The audit trail, which comes after 12b.
