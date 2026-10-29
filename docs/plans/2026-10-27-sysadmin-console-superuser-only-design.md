---
title: "Sysadmin Console PR 11: Superuser-only Design"
parent: Plans
---

# Sysadmin console PR 11: superuser-only

This replaces the break-glass design in `2026-10-26-sysadmin-console-pr11-break-glass-design.md`,
whose PR (#268) was closed unmerged. On 2026-10-27 the user rescoped the end of the migration:
"there's nothing changing, just deprecating django-admin. everything is moving to console. but
auth part can be the latest. console is also only for super admins".

## Slices left

- **PR 11, this one.** The console admits active superusers only. `/admin/` doesn't change, and
  the docs mark it deprecated.
- **PR 12, auth.** A console login and logout, plus a user list with create, edit, delete and
  password reset. Groups stay in the database with no console page. Then `/admin/` is removed.

## What exists

- `ConsoleView._refusal` (`apps/console/views/base.py`) admits active staff, then checks any
  one of the page's `read_permissions`.
- `apps/console/intents.py` holds about 25 permission constants. Each `Intent` carries a
  permission, and `resolve_action` renders text instead of a link when `Intent.allows` fails.
- Views, queries and templates carry `has_perm` checks and `can_add` or `can_change` flags.
  `build_reports` drops each card the user can't read.
- `apps/console/_tests/conftest.py` builds staff users with chosen permissions.

## Change

- `_refusal` admits `user.is_active and user.is_superuser`. A signed-in user who isn't one gets
  `templates/console/not_superuser.html` with status 403. It extends `base.html`, so the header's
  Sign out is the way out. Signed-out requests still go to the admin login, as today.
- An active superuser passes every `has_perm`, so each permission check is now always true.
  They're all deleted:
  - `read_permissions` and every permission constant
  - `Intent.permission`, `Intent.permissions` and `Intent.allows`. `resolve_action` keeps only
    the unknown-intent fallback.
  - each `has_perm` call in views and queries, and the flags and template branches they feed
  - `_reads` and the `None` cards in `build_reports`
- The POST-only views that subclass `View` directly use the same superuser check.
- The test fixtures become one superuser, plus one active non-superuser staff account for the
  refusal tests. Tests that only proved a permission refusal are deleted.

## Unchanged

- `/admin/`, its dashboard, its site and every `ModelAdmin`.
- The console's Admin link and the sign-in and sign-out through admin. PR 12 replaces those.

## Docs

- AGENTS.md convention 3: the console is the operations surface and is superuser-only.
  `/admin/` is deprecated and goes away once PR 12 moves auth.
- `apps/console/AGENTS.md`: the access rule, and the paragraphs about read and change
  permissions.
- `docs/Security.md` and `docs/Architecture.md` where they describe console access.

## Tests

- A superuser reads every page. Active staff, an inactive superuser and a plain user get the
  403 page. An htmx request from a signed-out user still gets `HX-Redirect`.
- The guard test covers the refusal page.
- 100% branch coverage on changed code.
