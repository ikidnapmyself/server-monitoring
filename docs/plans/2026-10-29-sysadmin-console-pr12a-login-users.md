---
title: "Sysadmin Console PR 12a: Login and Users"
parent: Plans
---

{% raw %}

# Console login and users implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The console signs superusers in and out on its own, and superusers manage user accounts in the console.

**Architecture:** Django's `LoginView` and `LogoutView` with a superuser-only form, rate limited by the existing `RateLimitMiddleware`. User pages follow the API key pages (`keys.py`, `key.py`, `key_delete.py` in views, `queries/keys.py`), with guards in `apps/console/queries/users.py` so every view shares them. `/admin/` stays untouched until 12b.

**Tech Stack:** Django 5.2, pytest-django, htmx, Primer CSS.

Design: `docs/plans/2026-10-29-sysadmin-console-auth-design.md`.

Rules for every task:

- TDD: failing test first, watch it fail, then implement.
- `uv run pytest apps/console config` after each code step. addopts already sets `-q`.
- Commit with plain `git commit` so pre-commit runs. The hook runs the full suite, which is slow.
  Messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage by name. Never stage `.agent-tree/` or
  `docs/plans/2026-09-14-refire-staleness-open-question.md`. Never push.
- No em or en dashes. Avoid gate, seam, wire, leverage, crucial, navigate, align. Minimal comments.
- Wording follows `apps/console/AGENTS.md`: no UUID, `snake_case` token or `{` outside
  `data-details`. The guard test enforces it.
- Don't edit `config/admin.py`, any `admin.py` or `templates/admin/`. 12b removes them.

Reference code on the closed #268 branch, read with `git show 2168299:<path>`:
`apps/console/forms.py`, `apps/console/views/login.py`, `templates/console/login.html`,
`apps/console/_tests/views/test_login.py`, `config/middleware/rate_limit.py`,
`config/_tests/test_settings.py`. That login admitted staff. Here it admits superusers.

---

### Task 1: Console login and logout

**Files:**
- Create: `apps/console/forms.py`, `apps/console/views/login.py`, `templates/console/login.html`
- Modify: `apps/console/urls.py`, `apps/console/views/__init__.py`, `apps/console/views/base.py`,
  `config/settings.py`, `config/middleware/rate_limit.py`, `templates/console/base.html`,
  `templates/console/not_superuser.html`, `static/console/console.css` if the login needs a rule
- Test: `apps/console/_tests/views/test_login.py`, `apps/console/_tests/views/test_base.py`,
  `apps/console/_tests/test_urls.py`, `apps/console/_tests/test_guard.py`,
  `config/_tests/test_settings.py`, the rate limiter's tests

**Step 1: Write the failing tests**

- A superuser posting the right password lands on `console:overview`. A `next` inside the
  console is honoured, and an outside `next` is not.
- Active staff who aren't superusers, a plain user, an inactive superuser and a wrong password
  all get status 200 with the one `REFUSED` message and no session.
- A signed-in superuser who GETs `console:login` is redirected to the overview.
- `console:logout` refuses GET with 405. POST signs out and redirects to `console:login`.
- A signed-out console GET redirects to `console:login?next=...`. A signed-out htmx request
  gets 204 with `HX-Redirect` to `console:login`.
- With `RATE_LIMIT_ENABLED=1`, the 11th POST to `/console/login/` within a minute gets 429, the
  "Too many sign-in attempts" text, and the username still in the field. Pin the clock the
  way #268's tests did, so the test can't straddle a minute boundary.
- `settings.LOGIN_URL == "console:login"` and `settings.LOGIN_REDIRECT_URL == "console:overview"`.
- The header's Sign out form and the refusal page's Sign out form both post to
  `console:logout`, and the header shows no Sign out on the login page.
- `test_urls.py`: the walk exempts exactly `console:login` and `console:logout` by name. Every
  other route must still be a `ConsoleView` with no `dispatch` of its own.
- `test_guard.py` renders the login page, blank and with a refusal.

**Step 2: Run them and watch them fail**

Run: `uv run pytest apps/console/_tests/views/test_login.py`
Expected: fails, `console:login` doesn't reverse.

**Step 3: Implement**

`apps/console/forms.py`:

```python
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from apps.console.views.base import admits

REFUSED = "Enter the username and password of an active superuser."


class ConsoleLoginForm(AuthenticationForm):
    """Signs in active superusers only. Every refusal reads the same, so none names an account."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": REFUSED,
        "inactive": REFUSED,
    }

    def confirm_login_allowed(self, user):
        if not admits(user):
            raise ValidationError(REFUSED, code="invalid_login")
```

If importing `admits` from the views package causes a cycle, move `admits` to a small module
such as `apps/console/access.py` and import it from there in both places.

`apps/console/views/login.py`: port `ConsoleLoginView` from #268 as is, with its
`rate_limited` classmethod. Add `ConsoleLogoutView(LogoutView)` with
`http_method_names = ["post", "options"]` and `next_page = "console:login"`, unless #268 used
`LogoutView.as_view(...)` in `urls.py`, in which case do the same.

`urls.py`: `path("login/", ConsoleLoginView.as_view(), name="login")` and
`path("logout/", ..., name="logout")`.

`settings.py`: `LOGIN_URL = "console:login"`, `LOGIN_REDIRECT_URL = "console:overview"`.

`views/base.py`: the signed-out branch uses `settings.LOGIN_URL` (through `redirect_to_login`'s
default) instead of `admin_url("login")`.

`rate_limit.py`: port #268's login limit for `/console/login/` only, with the view-class
`rate_limited` hook. Don't change the `/admin/` exemption, since 12b deletes it.

`templates/console/login.html`: port from #268 and keep the `refusal` block. It extends
`console/base.html` and blanks `{% block nav %}`. The header hides Sign out when the user is
signed out.

`base.html` and `not_superuser.html`: Sign out posts to `{% url 'console:logout' %}`. The
Admin link stays until 12b.

**Step 4: Run the tests**

Run: `uv run pytest apps/console config`
Expected: all pass.

**Step 5: Commit**

`feat(console): the console signs superusers in and out itself`

---

### Task 2: Users list

**Files:**
- Create: `apps/console/queries/users.py`, `apps/console/views/users.py`,
  `templates/console/users.html`, `templates/console/partials/_users.html`
- Modify: `apps/console/urls.py`, `apps/console/views/__init__.py`, `apps/console/intents.py`,
  the System subnav partial, `apps/console/_tests/fixtures.py` (`build_world()`)
- Test: `apps/console/_tests/queries/test_users.py`, `apps/console/_tests/views/test_users.py`,
  `apps/console/_tests/test_intents.py`, `apps/console/_tests/test_guard.py`

**Step 1: Write the failing tests**

- `build_user_list()` returns one row per user, sorted by username. Each row has the username,
  full name, email, last sign-in as relative time or "Never signed in", and a tone and label
  ("Active" or "Inactive"). A non-superuser row has the label "Can't sign in" and a
  `make_superuser` action.
- The row's link is a `view_user` intent to `console:user`. The page has an `add_user`
  action to `console:user-new`.
- `GET /console/users/` renders the list under System, with the Users tab current and placed
  after API keys.
- The System tabs test now expects Routes, Channels, Providers, API keys, Users.
- `build_world()` gains a superuser, an inactive user and a staff user who isn't a superuser,
  and the guard test renders `/console/users/`.

**Step 2: Run them and watch them fail**

**Step 3: Implement**

Follow `queries/keys.py` and `views/keys.py`. Register `view_user`, `add_user`, `edit_user`,
`set_user_password`, `delete_user` and `make_superuser` intents now, pointing at URL names
Tasks 3 and 4 add. Add those URL names now as well, to views that Tasks 3 and 4 fill in, so
`reverse` works. Or register only `view_user` and `add_user` here and add the rest in the
tasks that build their views. Pick the second if it keeps each commit green and covered.

**Step 4: Run the tests.** Expected: all pass.

**Step 5: Commit** `feat(console): a users page under System`

---

### Task 3: Create, edit and set password

**Files:**
- Create: `apps/console/views/user.py`, `apps/console/views/user_password.py`,
  `templates/console/user.html`, `templates/console/user_new.html`,
  `templates/console/user_password.html` and their partials
- Modify: `apps/console/forms.py`, `apps/console/queries/users.py`, `apps/console/urls.py`,
  `apps/console/views/__init__.py`, `apps/console/intents.py`
- Test: `apps/console/_tests/views/test_user.py`, `apps/console/_tests/views/test_user_password.py`,
  `apps/console/_tests/test_forms.py`, `apps/console/_tests/queries/test_users.py`

**Step 1: Write the failing tests**

- `GET /console/users/new/` shows username, email, first and last name, password and
  confirmation.
- A valid POST makes a user with `is_active`, `is_staff` and `is_superuser` all true, a usable
  password, and a 303 to that user's page with a success message.
- A duplicate username, mismatched passwords and a password that fails
  `AUTH_PASSWORD_VALIDATORS` each re-render the form with the field error and create nothing.
- `GET /console/users/<pk>/` shows the edit form: username, email, names and Active. A valid
  POST saves and stays on the page with a success `Notice`.
- Unticking Active on yourself is refused with the notice "You can't deactivate your own
  account." and nothing changes.
- Unticking Active on the last active superuser is refused with "At least one active superuser
  must remain." and nothing changes. Write this one with a second, inactive superuser present,
  so the check counts active superusers and not superusers.
- `GET /console/users/<pk>/password/` shows new password and confirmation. A valid POST sets
  it. Setting your own keeps the session (`update_session_auth_hash`): the next console GET is
  200, not a redirect.
- An unknown pk on each page gets the console's 404 page under System, with the Users section.

**Step 2: Run them and watch them fail**

**Step 3: Implement**

- `forms.py`: `UserCreateForm(UserCreationForm)` with the extra fields, saving the three flags.
  `UserEditForm(ModelForm)` for username, email, first_name, last_name and is_active.
  `UserPasswordForm` is Django's `SetPasswordForm`.
- `queries/users.py`: one function that answers why a change is refused, used by edit and by
  delete in Task 4:

```python
LAST_SUPERUSER = "At least one active superuser must remain."


def removal_refusal(actor, user) -> str:
    """Why ``actor`` can't deactivate or delete ``user``, or "" when they can."""
    if user.pk == actor.pk:
        return "You can't {verb} your own account."
    ...
```

  Shape the wording however reads best. The rules: the actor can't remove their own access,
  and the last active superuser can't lose theirs. Count inside the same transaction as the
  write and lock the superuser rows with `select_for_update()`, so two concurrent removals
  can't both pass.
- Views follow `views/key.py`: one view for new and existing, `landing_path`, `not_found`.

**Step 4: Run the tests.** Expected: all pass.

**Step 5: Commit** `feat(console): create and edit users, set passwords`

---

### Task 4: Delete and make superuser

**Files:**
- Create: `apps/console/views/user_delete.py`, `apps/console/views/user_promote.py`,
  `templates/console/user_delete.html` and its partial
- Modify: `apps/console/queries/users.py`, `apps/console/urls.py`,
  `apps/console/views/__init__.py`, `apps/console/intents.py`, `templates/console/user.html`
- Test: `apps/console/_tests/views/test_user_delete.py`,
  `apps/console/_tests/views/test_user_promote.py`

**Step 1: Write the failing tests**

- `GET /console/users/<pk>/delete/` confirms the deletion and says when the user last signed
  in. POST deletes and gives a 303 to `console:users` with a success message.
- Deleting yourself, and deleting the last active superuser, are refused on GET (no delete
  button, the reason shown) and on POST (nothing deleted, the reason as a message, 303 back to
  the user's page).
- `POST /console/users/<pk>/make-superuser/` sets `is_superuser` and `is_staff`, and gives a
  303 to the user's page with a success message. GET gets 405.
- The user page shows "Make superuser" only for a non-superuser, and Delete only when
  `removal_refusal` is empty.

**Step 2: Run them and watch them fail**

**Step 3: Implement** following `views/key_delete.py`.

**Step 4: Run the tests and coverage**

```bash
uv run pytest apps/console config
uv run coverage run -m pytest apps/console config && uv run coverage report --include="apps/console/*,config/*" --show-missing --skip-covered
```

Expected: all pass, and no changed line is missing.

**Step 5: Commit** `feat(console): delete users and make superusers`

---

### Task 5: Docs

**Files:** `apps/console/AGENTS.md`, `AGENTS.md` convention 3, `docs/Security.md`,
`docs/Setup-Guide.md` and `docs/Deployment.md` where they say to sign in at `/admin/`.

- The console has its own login at `/console/login/`, for active superusers, limited to 10
  tries a minute when `RATE_LIMIT_ENABLED=1`.
- Users are managed at `/console/users/`. The two guards. Groups have no page.
- `manage.py createsuperuser` makes the first account.
- `/admin/` is still there and deprecated, and 12b removes it.

Voice check:

```bash
git diff origin/main -- '*.md' | grep -nP "^\+.*(\x{2014}|\x{2013}|\bgate\b|\bseam\b|\bwire\b|leverage|crucial|navigate|\balign)"
```

Expected: only the grep line in this plan.

Commit with `--no-verify` (docs only): `docs: the console signs people in and manages users`

---

### Task 6: Verify

1. `uv run pytest`: all pass.
2. `uv run coverage run -m pytest && uv run coverage report` on changed files: 100% on
   changed lines.
3. `uv run ruff check . && uv run black . --check && uv run mypy .`: clean.
4. Screenshots at 1280 and 375 wide: the login page blank, refused and rate limited, the users
   list, a new user form with errors, the password page, and a refused delete.

{% endraw %}
