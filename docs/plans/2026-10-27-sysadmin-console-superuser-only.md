---
title: "Sysadmin Console PR 11: Superuser-only"
parent: Plans
---

# Console superuser-only implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Only active superusers can use `/console/`. Every per-permission check in the console is deleted.

**Architecture:** One check in `ConsoleView._refusal` and in the POST-only views that don't subclass it. Everything that branched on `has_perm` is now always true and goes. `/admin/` isn't touched.

**Tech Stack:** Django 5.2, pytest-django, htmx, Primer CSS.

Design: `docs/plans/2026-10-27-sysadmin-console-superuser-only-design.md`.

Rules for every task:

- Run `uv run pytest apps/console` after each step that changes code. addopts already sets `-q`.
- Commit with `git commit` so pre-commit runs. Messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never stage `.agent-tree/` or `docs/plans/2026-09-14-refire-staleness-open-question.md`.
- No em or en dashes in code, docs or messages. Minimal comments.

---

### Task 1: Superusers only

**Files:**
- Modify: `apps/console/views/base.py`
- Create: `templates/console/not_superuser.html`
- Modify: every POST-only view that subclasses `View` directly: `alert_reeval.py`,
  `incident_action.py`, `incident_rerun.py`, `node_allow_ports.py`, `node_policy.py`,
  `node_reeval.py`, `nodes.py`. Check each. Some may already reuse `ConsoleView._refusal`.
- Modify: `apps/console/_tests/conftest.py` and the tests that use its fixtures
- Test: `apps/console/_tests/views/test_base.py` (or the file that already tests `_refusal`)

**Step 1: Write the failing tests**

- An active superuser gets 200 on the overview.
- Active staff who aren't superusers, an inactive superuser and an active plain user each get
  403. The response uses `console/not_superuser.html` and contains a Sign out form.
- A signed-out GET still redirects to the admin login. A signed-out htmx GET still gets 204
  with `HX-Redirect`.
- Each POST-only view refuses active staff who aren't superusers with 403.

**Step 2: Run them and watch them fail**

Run: `uv run pytest apps/console/_tests/views/test_base.py`
Expected: the staff case gets 200, not 403.

**Step 3: Implement**

In `_refusal`, replace the staff check and the `read_permissions` check with:

```python
if not (request.user.is_active and request.user.is_superuser):
    return render(request, "console/not_superuser.html", status=403)
```

`not_superuser.html` extends `console/base.html`. Body: a heading "Superusers only" and one
sentence: "The console is for superusers. Ask one to give your account superuser access, or
sign out and sign in as a superuser." The header already holds Sign out. Match the markup of
`not_found.html`.

Give the POST-only views the same check, reusing one helper rather than copying the condition.

**Step 4: Switch the fixtures**

In `conftest.py`, the user every test signs in with becomes an active superuser. Add an
`active_staff` fixture for the refusal tests. Delete fixtures that only grant a chosen
permission, and point their tests at the superuser. Delete each test whose only claim is
that a missing permission hides a link, a button, a card or a page. Keep its positive twin.

**Step 5: Run the console suite**

Run: `uv run pytest apps/console`
Expected: all pass.

**Step 6: Commit**

`feat(console): only active superusers can use the console`

---

### Task 2: Delete the permission plumbing

**Files:**
- Modify: `apps/console/intents.py`, `apps/console/templatetags/console.py`
- Modify: every `apps/console/views/*.py` and `apps/console/queries/*.py` that calls
  `has_perm`, sets `read_permissions` or passes a `can_*` flag
- Modify: the templates that branch on those flags: `channels.html`, `keys.html`,
  `providers.html`, `routes.html`, `partials/_allow_ports.html`, `partials/_channel.html`,
  `partials/_key.html`, `partials/_provider.html`, `partials/_route.html`, and any others
  `git grep` finds
- Modify: the matching tests

**Step 1: Find everything**

```bash
git grep -n "has_perm\|read_permissions\|_READ\b\|_ADD\b\|_CHANGE\b\|_DELETE\b\|PERMISSION\|\.allows(\|\.permissions\b\|can_add\|can_change\|can_edit\|can_delete\|_reads(" -- apps/console templates/console
```

`can_rerun(incident)` in `views/incident_rerun.py` is a rule about the incident, not a
permission. Keep it.

**Step 2: Delete**

- `intents.py`: every permission constant, and `Intent.permission`, `permissions` and `allows`.
  `Intent` keeps `url` and `method`. `resolve_action` returns the text fallback only for an
  unknown intent. Update the registry rows.
- Views: every `read_permissions`, every `has_perm` branch and the refusal it guarded. A
  view that only refused on a permission now just does its work.
- Queries: `can_*` parameters go. `build_reports(user, now)` becomes `build_reports(now)` and
  every card is always built, so `Reports` fields stop being optional. `_reads` goes.
- Templates: drop the `{% if can_* %}` wrappers and keep their content.
- Tests: drop arguments and assertions about removed parameters. Delete tests of removed
  branches.

**Step 3: Confirm nothing is left**

Re-run the Step 1 grep. Expected: only `can_rerun` and unrelated matches.

**Step 4: Run the console suite and coverage**

```bash
uv run pytest apps/console
uv run coverage run -m pytest apps/console && uv run coverage report --include="apps/console/*" --show-missing
```

Expected: all pass, and no changed line is missing.

**Step 5: Commit**

`refactor(console): drop per-permission checks, a superuser passes them all`

---

### Task 3: Docs

**Files:**
- Modify: `AGENTS.md` convention 3, and the `console` row in the core apps table ("staff only"
  becomes "superusers only")
- Modify: `apps/console/AGENTS.md`: the access rule, the incident action paragraph ("checks
  active staff first, then the page's read permissions") and the rerun paragraph
  ("needs `checkers.add_checkrun`"), plus any other permission wording `git grep -n
  "permission\|staff" apps/console/AGENTS.md apps/console/README.md` turns up
- Modify: `docs/Security.md`, `docs/Architecture.md` where they describe console access

**Step 1: Edit**

Convention 3 says the console at `/console/` is the operations surface, superusers only.
`/admin/` is deprecated: it still works as on main, and goes away once the auth slice moves
the login and user management into the console. Keep the rest of the convention's facts about
`MonitoringAdminSite` and the dashboard, since they're still true.

**Step 2: Check the voice**

```bash
git diff origin/main -- '*.md' | grep -nP "^\+.*(\x{2014}|\x{2013}|\bgate\b|\bseam\b|\bwire\b|leverage|crucial|navigate|\balign)"
```

Expected: no output.

**Step 3: Commit**

`docs: the console is superuser-only, /admin/ is deprecated`

---

### Task 4: Verify

**Step 1:** `uv run pytest`. Expected: all pass.

**Step 2:** `uv run coverage run -m pytest && uv run coverage report` for the changed files.
Expected: 100% on changed lines.

**Step 3:** `uv run ruff check . && uv run black . --check && uv run mypy .`. Expected: clean.

**Step 4:** Run the server, sign in as a staff account that isn't a superuser and screenshot
the refusal page. Then sign in as a superuser and screenshot the overview, a list with an
Add button, and the reports page.
