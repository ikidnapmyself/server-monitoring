---
title: "Sysadmin Console PR 11: Break-glass"
parent: Plans
---

{% raw %}

# Sysadmin console PR 11: break-glass Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The console becomes the only operator surface. It gets its own login at
`/console/login/`. `/admin/` becomes stock Django for active superusers, with every model
editable and every secret field hidden. The admin dashboard, its skin and its helpers are
deleted, and the readiness and fleet code they held moves to where its readers live.

**Architecture:**
- Sign-in is Django's `LoginView` and `LogoutView` under the `console` namespace, with a
  Primer template and a form that admits only active staff. `LOGIN_URL` points there, so
  `ConsoleView` redirects through `redirect_to_login` with no URL of its own.
- `config/admin.py` keeps one small `AdminSite` subclass whose `has_permission` admits an
  active superuser and nobody else. Each app's `admin.py` registers plain `ModelAdmin`s.
- `config/dashboard.py` splits in three. Readiness moves to `config/readiness.py`, because
  `apps.alerts` reads `NODE_RECENT_MINUTES`. Fleet metrics move to
  `apps/console/queries/fleet.py`, next to the Reports query that reads them. The rest is
  deleted.
- `config/admin_badges.py` becomes `config/tones.py`, without the two HTML helpers only
  `ops.css` styled.

Design: `docs/plans/2026-10-26-sysadmin-console-pr11-break-glass-design.md`.

**Tech stack:** Django 5.2 auth views and admin, templates, and pytest-django.

**Rules for every task:**
- Use absolute imports. No comments that restate code. Line length is 100.
- Run every command from the worktree root, on branch `feat/console-break-glass`.
- Run `uv run black . && uv run ruff check . --fix` before each commit.
- Pre-commit runs black, ruff, the full pytest suite and mypy, so a commit takes 20 minutes
  or more. Run it in the background.
- pytest's `addopts` already passes `-q`. Don't add another.
- End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Voice, in code, strings and docs: no em or en dashes. None of the words gate, seam, wire,
  leverage, crucial, navigate or align.
- Never edit `docs/plans/*` other than this plan.

## Where this plan corrects the design

1. **No URL in `ConsoleView`.** The design has the console redirect "there". The view now
   calls `redirect_to_login(next)` with no `login_url`, which resolves `settings.LOGIN_URL`.
   `admin_url()` in `intents.py` stays, but only for the header's Admin link, so its
   docstring and test drop login and logout.
2. **No middleware change.** `/console/login/` is not under `API_PATH_PREFIXES`, so
   `APIKeyAuthMiddleware` passes it through, and `RateLimitMiddleware` only counts the
   prefixes in `RATE_LIMITS`. `EXEMPT_PATH_PREFIXES` stays `("/admin/", "/static/")`.
   `ConsoleHeadersMiddleware` already covers the login page, so the template carries no
   inline script or style.
3. **One refusal message.** A wrong password, an inactive account and a non-staff account
   all read "Enter the username and password of an active staff account." A separate
   message would tell a stranger which usernames exist. `ModelBackend` refuses an inactive
   user before `confirm_login_allowed` runs, so the form's own check catches non-staff.
4. **Logout lands on the login.** The design doesn't say where. `LogoutView` gets
   `next_page="console:login"`. It answers only POST, as Django 5 does.
5. **Three admins keep add refused.**
   - `APIKey` generates its key in `save()`, and the raw token is shown once, by the
     console's create page. A key added in the admin could never be read back.
   - `NotificationChannel` and `IntelligenceProvider` can't take their `config` in the
     admin. A row added there has no settings, and the console's add flow validates the
     driver's form. Change and delete stay open.
   - Their `driver` and `provider` fields are read-only on change, because the stored
     `config` is shaped by that kind.
6. **The `InboxItem` proxy is no longer registered.** It shows the same rows as
   `PipelineRun`. Django creates a proxy's permissions whether or not it's registered, so
   `orchestration.view_inboxitem` and `change_inboxitem` still exist for the console.
7. **Large foreign keys become raw ids.** An editable change form renders every incident,
   alert or run in a `<select>`. `raw_id_fields` covers `Alert.node`, `Alert.incident`,
   `Incident.node`, `AlertHistory.alert`, `CheckRun.alert`, `AnalysisRun.incident`,
   `PipelineRun.node`, `PipelineRun.incident` and `StageExecution.pipeline_run`.
8. **`NodeMetrics.url` goes.** Only `templates/admin/dashboard.html` read it. Reports links
   a node through the `view_node` intent. With `config.dashboard` gone,
   `build_fleet_metrics` imports `node_overview` at module level.
9. **Tests the inventory missed:**
   - `config/_tests/test_dashboard_readiness.py:127` builds the dashboard context. It
     becomes a `build_readiness()` test.
   - `config/_tests/test_settings.py:105` collects `admin/css/ops.css`.
   - `test_middleware.py:67` and the two middleware tests that GET `/admin/login/` stay.
     That page still exists.
   - `config/_tests/test_netmap.py:388` checks `"admin" not in source`, since the map
     builds no admin link of any kind.
10. **Stale comments the inventory missed:**
    - `config/dashboard.py:60` says a hub "reads the admin daily".
    - `apps/alerts/_tests/test_node_overview.py:55` names `config/dashboard.py`.
    - `apps/notify/README.md:297` names the admin readiness panel.
    - `docs/Security.md:120,161` and the ISO table rows A.5.15, A.8.2, A.8.3 and A.8.26.
    - `docs/Deployment.md:583`.
    - `apps/console/AGENTS.md:13,17,53,189,252,281`.
11. **`node_policy.py:401` claims an import cycle** through `config.dashboard` that the
    import graph doesn't show. The sentence is reworded to the real reason, which is
    weight.

---

### Task 1: The console login

**Files:**
- Create: `apps/console/forms.py`, `apps/console/views/login.py`, `templates/console/login.html`
- Modify: `apps/console/urls.py`, `apps/console/views/__init__.py`, `apps/console/views/base.py`,
  `apps/console/intents.py`, `templates/console/base.html`, `static/console/console.css`,
  `config/settings.py`
- Test: `apps/console/_tests/views/test_login.py` (new), `apps/console/_tests/test_guard.py`,
  `apps/console/_tests/views/test_overview.py`, `apps/console/_tests/test_intents.py`,
  `config/_tests/test_settings.py`, and every file under `apps/console/_tests/views/` that
  asserts `admin:login`

**Step 1: Write the failing tests**

`apps/console/_tests/views/test_login.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.console.forms import REFUSED

PASSWORD = "pw-correct-horse"


def _user(**flags):
    return get_user_model().objects.create_user("op", password=PASSWORD, **flags)


def _sign_in(client, password=PASSWORD, **extra):
    return client.post(
        reverse("console:login"), {"username": "op", "password": password, **extra}
    )


@pytest.mark.django_db
def test_the_login_page_renders_for_anyone(client):
    resp = client.get(reverse("console:login"))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Sign in to the console" in body
    assert 'name="username"' in body
    assert 'name="password"' in body
    assert 'name="csrfmiddlewaretoken"' in body
    assert reverse("console:logout") not in body
    assert 'class="c-unav"' not in body
    assert "Content-Security-Policy" in resp


@pytest.mark.django_db
def test_staff_signs_in_and_lands_on_next(client):
    _user(is_staff=True)

    resp = _sign_in(client, next="/console/incidents/?q=disk")

    assert resp.status_code == 302
    assert resp["Location"] == "/console/incidents/?q=disk"
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_staff_without_next_lands_on_the_overview(client):
    _user(is_staff=True)

    assert _sign_in(client)["Location"] == reverse("console:overview")


@pytest.mark.django_db
def test_a_foreign_next_lands_on_the_overview(client):
    _user(is_staff=True)

    assert _sign_in(client, next="https://evil.example/")["Location"] == reverse(
        "console:overview"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("flags", "password"),
    [
        ({}, PASSWORD),
        ({"is_staff": True, "is_active": False}, PASSWORD),
        ({"is_staff": True}, "wrong"),
    ],
    ids=["not-staff", "inactive", "wrong-password"],
)
def test_every_refusal_reads_the_same(client, flags, password):
    _user(**flags)

    resp = _sign_in(client, password=password)

    assert resp.status_code == 200
    assert REFUSED in resp.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_logout_signs_out_and_returns_to_the_login(client, staff):
    resp = client.post(reverse("console:logout"))

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:login")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_logout_refuses_a_get(client, staff):
    assert client.get(reverse("console:logout")).status_code == 405
```

In `apps/console/_tests/test_guard.py`, import `REFUSED` from `apps.console.forms` and add:

```python
@pytest.mark.django_db
def test_the_login_page_leaks_nothing(client):
    page = visible(_get(client, "/console/login/", {}))
    refused = client.post("/console/login/", {"username": "nobody", "password": "x"})
    assert refused.status_code == 200
    refusal = visible(refused.content.decode())

    _assert_no_leak("/console/login/", page)
    _assert_no_leak("/console/login/ refused", refusal)
    assert "Sign in to the console" in page
    assert REFUSED in refusal
```

In `apps/console/_tests/views/test_overview.py`, the last two lines of
`test_page_polls_itself_and_links_onward` become:

```python
    assert f'action="{reverse("console:logout")}"' in body
    assert reverse("admin:index") not in body
```

and add:

```python
@pytest.mark.django_db
def test_a_superuser_sees_the_admin_link(client, superuser):
    client.force_login(superuser)

    body = client.get(reverse("console:overview")).content.decode()

    assert f'href="{reverse("admin:index")}"' in body
```

In `apps/console/_tests/test_intents.py`, `test_admin_url_names_the_admins_own_pages`
becomes:

```python
def test_admin_url_names_the_admin_index():
    assert admin_url("index") == reverse("admin:index")
```

In `config/_tests/test_settings.py`, add:

```python
def test_the_console_owns_sign_in():
    from django.conf import settings
    from django.shortcuts import resolve_url

    assert resolve_url(settings.LOGIN_URL) == "/console/login/"
    assert resolve_url(settings.LOGIN_REDIRECT_URL) == "/console/"
```

Point every redirect assertion at the console login. `test_middleware.py` is left alone,
because it checks that `/admin/login/` sits outside the console:

```bash
grep -rl "admin:login\|_admin_login" apps/console/_tests/views | xargs sed -i '' \
  -e "s/reverse('admin:login')/reverse('console:login')/g" \
  -e 's/reverse("admin:login")/reverse("console:login")/g' \
  -e 's/_admin_login/_console_login/g'
grep -rn "admin:login" apps/console/_tests
```

Expected: the grep prints only `apps/console/_tests/test_middleware.py:67`.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest apps/console/_tests/views/test_login.py apps/console/_tests/views/test_overview.py config/_tests/test_settings.py
```

Expected: FAIL. The login tests stop at `ModuleNotFoundError: No module named
'apps.console.forms'`. The redirect tests fail with `NoReverseMatch` for `login` in the
`console` namespace.

**Step 3: Implement**

`apps/console/forms.py`:

```python
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

REFUSED = "Enter the username and password of an active staff account."


class ConsoleLoginForm(AuthenticationForm):
    """Signs in active staff only. Every refusal reads the same, so none names an account."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": REFUSED,
        "inactive": REFUSED,
    }

    def confirm_login_allowed(self, user):
        if not (user.is_active and user.is_staff):
            raise ValidationError(REFUSED, code="invalid_login")
```

`apps/console/views/login.py`:

```python
from django.contrib.auth.views import LoginView

from apps.console.forms import ConsoleLoginForm


class ConsoleLoginView(LoginView):
    """Django's own login view, so it isn't a ``ConsoleView``: anyone may open it."""

    template_name = "console/login.html"
    authentication_form = ConsoleLoginForm
```

Add `from apps.console.views.login import ConsoleLoginView` to `views/__init__.py` and
`"ConsoleLoginView"` to `__all__`, in alphabetical place.

`apps/console/urls.py`: import `LogoutView` from `django.contrib.auth.views` and
`ConsoleLoginView` from `apps.console.views`. After the `overview` path add:

```python
    path("login/", ConsoleLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="console:login"), name="logout"),
```

`config/settings.py`, after `ROOT_URLCONF = "config.urls"`:

```python
LOGIN_URL = "console:login"
LOGIN_REDIRECT_URL = "console:overview"
```

`apps/console/views/base.py`: delete `from apps.console.intents import admin_url`, and the
anonymous branch of `_refusal` becomes:

```python
        if not request.user.is_authenticated:
            if not self.is_htmx:
                return redirect_to_login(self._return_path(request))
            # htmx would swap a followed redirect's login form into the page.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect_to_login(self._page_path(request)).url
            return response
```

`apps/console/intents.py`: `admin_url`'s docstring becomes
`"""The admin index, which the console header links for superusers."""`.

`templates/console/base.html`: wrap the header links and the nav in blocks, show Admin to
superusers only, and post logout to the console. The header links become:

```html
    {% block links %}<div class="c-header__links">
      {% if user.is_superuser %}<a class="c-header__link" href="{% admin_url 'index' %}">Admin</a>{% endif %}
      <form method="post" action="{% url 'console:logout' %}">
        {% csrf_token %}
        <button class="c-header__link" type="submit">Sign out</button>
      </form>
    </div>{% endblock %}
```

Put `{% block nav %}` before `<nav class="c-unav" aria-label="Console">` and
`{% endblock %}` after its `</nav>`.

`templates/console/login.html`:

```html
{% extends "console/base.html" %}
{% block title %}Sign in · Console{% endblock %}
{% block links %}{% endblock %}
{% block nav %}{% endblock %}
{% block content %}
<div class="c-login">
  <h1 class="c-page-title">Sign in to the console</h1>
  <form method="post" action="{% url 'console:login' %}" data-disable-on-submit>
    {% csrf_token %}
    <input type="hidden" name="next" value="{{ next }}">
    {% for error in form.non_field_errors %}<p class="c-field__error c-tone-critical">{{ error }}</p>{% endfor %}
    <div id="login-fields" class="c-box c-form__group"><div class="c-box__body">
      {% include "console/partials/_field.html" with field=form.username %}
      {% include "console/partials/_field.html" with field=form.password %}
    </div></div>
    <button id="login-submit" class="c-button c-button--primary" type="submit">Sign in</button>
  </form>
</div>
{% endblock %}
```

`static/console/console.css`, under `/* Page */` after `.c-main`:

```css
.c-login { max-width: 340px; margin: var(--space-8) auto 0; }
```

**Step 4: Run the tests to see them pass**

```bash
uv run pytest apps/console/_tests config/_tests/test_settings.py
```

Expected: PASS. `test_templates.py` passes on `login.html` too, since it holds no inline
script, style or handler.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A apps/console templates/console static/console config/settings.py config/_tests/test_settings.py
git commit -m "feat(console): the console has its own login"
```

---

### Task 2: Readiness moves to `config/readiness.py`

**Files:**
- Create: `config/readiness.py`
- Modify: `config/dashboard.py`, `apps/alerts/node_overview.py`,
  `apps/console/queries/nodes.py`, `apps/console/queries/overview.py`
- Move: `config/_tests/test_dashboard_readiness.py` → `config/_tests/test_readiness.py`
- Test: `config/_tests/test_readiness.py`, `apps/alerts/_tests/test_node_overview.py`,
  `apps/console/_tests/queries/test_overview.py`

**Step 1: Move the tests and point them at the new module**

```bash
git mv config/_tests/test_dashboard_readiness.py config/_tests/test_readiness.py
sed -i '' \
  -e 's/from config\.dashboard import/from config.readiness import/' \
  -e 's/from config import dashboard$/from config import readiness/' \
  -e 's/monkeypatch\.setattr(dashboard,/monkeypatch.setattr(readiness,/' \
  -e 's/^        dashboard,$/        readiness,/' \
  config/_tests/test_readiness.py
sed -i '' 's/from config\.dashboard import NODE_RECENT_MINUTES/from config.readiness import NODE_RECENT_MINUTES/' \
  apps/alerts/_tests/test_node_overview.py apps/console/_tests/queries/test_overview.py
grep -n "dashboard" config/_tests/test_readiness.py
```

The grep shows the top import, which still names `get_dashboard_context`, and
`test_readiness_in_dashboard_context`. Drop `get_dashboard_context` from the import and
replace that test with:

```python
@pytest.mark.django_db
def test_readiness_has_an_entry_per_panel_card():
    readiness = build_readiness()
    for entry in readiness:
        assert {"key", "label", "status", "detail", "url"} <= set(entry)
    keys = {entry["key"] for entry in readiness}
    assert {"channels", "provider", "preflight", "inbox", "nodes"} <= keys
```

In `apps/alerts/_tests/test_node_overview.py:55`, the comment names
`config/readiness.py`.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest config/_tests/test_readiness.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'config.readiness'`.

**Step 3: Move the code verbatim**

```bash
uv run python - <<'EOF'
from pathlib import Path

src = Path("config/dashboard.py")
text = src.read_text()
start = text.index("NODE_RECENT_MINUTES = 15")
end = text.index("def _row(row, user) -> dict:")
header = '''"""Configuration readiness: what still needs setting up, and the fix for each fault.

The console Overview reads ``keyed_readiness``. ``apps.alerts`` reads
``NODE_RECENT_MINUTES``, so this lives in ``config`` rather than in the console.
"""

from datetime import timedelta

from django.utils.text import slugify

from config.describing import Action, Description

'''
Path("config/readiness.py").write_text(header + text[start:end].rstrip() + "\n")
src.write_text(text[:start] + "from config.readiness import build_readiness\n\n\n" + text[end:])
EOF
```

Then tidy by hand:
- In `config/readiness.py`, `build_readiness`'s docstring says "for the console Overview"
  instead of "for the dashboard". The comment "A hub that reads the admin daily" reads "A
  hub that reads the console daily".
- In `config/dashboard.py`, move the new `from config.readiness import build_readiness`
  line up into the import block.

```bash
uv run ruff check config/dashboard.py config/readiness.py --fix
sed -i '' 's/from config\.dashboard import/from config.readiness import/' \
  apps/alerts/node_overview.py apps/console/queries/nodes.py apps/console/queries/overview.py
```

Ruff drops `slugify`, `Action`, `Description` and `timedelta` from `dashboard.py`. In
`apps/alerts/node_overview.py:50` the docstring names ``config/readiness.py``.

**Step 4: Run the tests to see them pass**

```bash
uv run pytest config/_tests apps/alerts/_tests/test_node_overview.py apps/console/_tests
grep -rn "config\.dashboard import NODE_RECENT\|config\.dashboard import .*keyed" apps config
```

Expected: PASS, and the grep prints nothing.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A config apps/alerts apps/console
git commit -m "refactor: readiness lives in config/readiness.py"
```

---

### Task 3: Fleet metrics move to `apps/console/queries/fleet.py`

**Files:**
- Create: `apps/console/queries/fleet.py`
- Modify: `config/dashboard.py`, `apps/console/queries/reports.py`
- Create: `apps/console/_tests/queries/test_fleet.py`
- Modify: `config/_tests/test_dashboard.py`

**Step 1: Move the tests**

Cut `TestFleetMetrics` out of `config/_tests/test_dashboard.py`, from its
`@pytest.mark.django_db` decorator to the blank lines before the
`TestTrendRowsAreReachable` decorator, and paste it into
`apps/console/_tests/queries/test_fleet.py` under:

```python
"""The fleet grid's shaping: which columns exist, and what a node missing one gets."""

import pytest

from apps.console.queries.fleet import build_fleet_metrics
```

In the new file:
- Delete every `from config.dashboard import build_fleet_metrics` line.
- Delete `test_the_row_links_to_the_node`. Task 5 removes `NodeMetrics.url`, and
  `apps/console/_tests/queries/test_reports.py` already pins the node link.
- The class docstring reads "The headline readings grid on Reports." and "the shaping the
  grid adds" instead of "the dashboard".

**Step 2: Run the tests to see them fail**

```bash
uv run pytest apps/console/_tests/queries/test_fleet.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'apps.console.queries.fleet'`.

**Step 3: Move the code verbatim**

```bash
uv run python - <<'EOF'
from pathlib import Path

src = Path("config/dashboard.py")
text = src.read_text()
start = text.index("# The unit each headline metric")
header = '''"""Each node's current headline reading per checker, for the Reports fleet grid."""

from dataclasses import dataclass

from django.urls import reverse

from apps.alerts.models import Node
from apps.alerts.node_overview import build_checker_rows
from apps.alerts.reevaluation import PRIMARY_METRIC

'''
Path("apps/console/queries/fleet.py").write_text(header + text[start:])
src.write_text(text[:start].rstrip() + "\n")
EOF
```

In `apps/console/queries/fleet.py`, `build_fleet_metrics` loses its function-level
imports and the docstring line that explained them. Delete:

```python
    from apps.alerts.models import Node
    from apps.alerts.node_overview import build_checker_rows
    from apps.alerts.reevaluation import PRIMARY_METRIC

```

and the docstring line `Imported inside the function because ``node_overview`` imports
this module.`

In `config/dashboard.py`, `get_dashboard_context` imports
`from apps.console.queries.fleet import build_fleet_metrics` next to its Reports imports.

In `apps/console/queries/reports.py`, the import becomes
`from apps.console.queries.fleet import METRIC_UNITS, build_fleet_metrics`.

```bash
uv run ruff check config/dashboard.py apps/console/queries --fix
```

**Step 4: Run the tests to see them pass**

```bash
uv run pytest apps/console/_tests config/_tests
```

Expected: PASS. `test_dashboard_render.py` still renders the grid with each node's link.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A config apps/console
git commit -m "refactor(console): fleet metrics live with the Reports queries"
```

---

### Task 4: `config/admin_badges.py` becomes `config/tones.py`

**Files:**
- Move: `config/admin_badges.py` → `config/tones.py`
- Move: `config/_tests/test_admin_badges.py` → `config/_tests/test_tones.py`
- Modify: every importer, `config/_tests/test_no_inline_colour.py`, `apps/console/AGENTS.md:65`

**Step 1: Move the test and cut the helper tests**

```bash
git mv config/_tests/test_admin_badges.py config/_tests/test_tones.py
```

In `test_tones.py`:
- The docstring reads `"""The tone tables: one tone per domain status."""`.
- The import becomes `from config.tones import (...)` without `badge` and `tinted`.
- Drop the `format_html` and `SafeString` imports.
- Delete every `test_badge_*` and `test_tinted_*` test. Keep the tone, `tone_for` and table
  tests.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest config/_tests/test_tones.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'config.tones'`.

**Step 3: Rename, then cut `badge()` and `tinted()`**

```bash
git mv config/admin_badges.py config/tones.py
grep -rl "config\.admin_badges" apps config --include='*.py' | xargs sed -i '' 's/config\.admin_badges/config.tones/g'
grep -rn "admin_badges\|badge(\|tinted(" apps config --include='*.py'
```

Expected: the grep prints only lines in `config/_tests/test_no_inline_colour.py`.

`config/tones.py` keeps `TONES`, the four tables and `tone_for`. It loses `format_html`,
`SafeString`, `_check`, `badge` and `tinted`. Its docstring and the table comment become:

```python
"""Which of five tones each domain status reads in.

A ``Description`` carries a tone name. The console's ``c-tone-<tone>`` classes in
``static/console/console.css`` give each tone its colour.
"""

TONES = frozenset({"critical", "warning", "info", "ok", "muted"})


# Keyed on the raw string values rather than the enum members: the apps import these
# tables, so importing an app enum here would close a config -> apps -> config cycle at
# module load. ``config/_tests/test_tones.py`` asserts each table still covers its enum.
```

`config/_tests/test_no_inline_colour.py`:
- `_scanned()` adds `ROOT / "config" / "tones.py"`, because `config.glob("admin*.py")` no
  longer matches it.
- `MUST_BE_SCANNED` names `"config/tones.py"` instead of `"config/admin_badges.py"`.

`apps/console/AGENTS.md:65`: "Tones come from `config/tones.py`."

**Step 4: Run the tests to see them pass**

```bash
uv run pytest config/_tests apps/console/_tests apps/alerts/_tests apps/checkers/_tests
```

Expected: PASS.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A config apps
git commit -m "refactor: tone tables live in config/tones.py"
```

---

### Task 5: The admin is superuser break-glass, and the dashboard goes

**Files:**
- Modify: `config/admin.py`
- Delete: `config/dashboard.py`, `config/admin_links.py`, `templates/admin/dashboard.html`,
  `templates/admin/base_site.html`, `static/admin/css/ops.css`
- Delete: `config/_tests/test_admin_links.py`, `config/_tests/test_admin_site.py`,
  `config/_tests/test_dashboard.py`, `config/_tests/test_dashboard_render.py`,
  `config/_tests/test_ops_css.py`
- Modify: `apps/console/queries/fleet.py`, `apps/console/queries/reports.py:3`,
  `config/describing.py:5`, `apps/console/AGENTS.md:9`
- Test: `config/_tests/test_admin.py` (rewritten), `config/_tests/test_describing.py`,
  `apps/orchestration/_tests/test_admin.py`, `config/_tests/test_settings.py`,
  `config/_tests/test_no_inline_colour.py`, `config/_tests/test_netmap.py`

**Step 1: Write the failing tests**

Replace `config/_tests/test_admin.py` with:

```python
"""The admin is stock Django for active superusers, and nothing else."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse

from config.admin import MonitoringAdminSite

pytestmark = pytest.mark.django_db


def test_the_default_site_is_the_break_glass_site():
    assert isinstance(admin.site, MonitoringAdminSite)
    assert admin.site.index_template is None
    assert admin.site.site_url == "/console/"


def test_staff_with_every_permission_but_no_superuser_flag_is_turned_away(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    user.user_permissions.set(Permission.objects.all())
    client.force_login(user)

    for url in (
        reverse("admin:index"),
        reverse("admin:notify_notificationchannel_changelist"),
    ):
        resp = client.get(url)
        assert resp.status_code == 302, url
        assert resp["Location"].startswith(reverse("admin:login")), url


def test_an_inactive_superuser_is_turned_away(rf):
    request = rf.get("/admin/")
    request.user = get_user_model()(username="gone", is_superuser=True, is_active=False)

    assert not admin.site.has_permission(request)


def test_a_superuser_gets_the_stock_index(admin_client):
    resp = admin_client.get(reverse("admin:index"))

    assert resp.status_code == 200
    assert resp.templates[0].name == "admin/index.html"
    body = resp.content.decode()
    assert "ops.css" not in body
    assert "Readiness" not in body
    assert reverse("admin:alerts_incident_changelist") in body
```

In `config/_tests/test_describing.py`, add:

```python
def test_nothing_in_config_imports_the_console():
    import re
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR) / "config"
    pattern = re.compile(r"^\s*(?:from|import) apps\.console", re.M)
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "_tests" not in path.parts and pattern.search(path.read_text())
    ]

    assert offenders == []
```

`config/_tests/test_netmap.py:388` becomes `assert "admin" not in source`.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest config/_tests/test_admin.py config/_tests/test_describing.py
```

Expected: FAIL. `index_template` is `"admin/dashboard.html"`, `site_url` is `"/"`, staff
reach the index, and `test_nothing_in_config_imports_the_console` names `dashboard.py`.

**Step 3: Implement**

`config/admin.py` becomes:

```python
"""The break-glass admin: stock Django, for active superusers only."""

from django.contrib import admin
from django.contrib.admin import AdminSite


class MonitoringAdminSite(AdminSite):
    site_header = "Server Monitoring break-glass admin"
    site_title = "Server Monitoring"
    index_title = "Raw rows. An edit here skips the console's checks."
    site_url = "/console/"

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


class APIKeyAdmin(admin.ModelAdmin):
    """Read-only. Operators edit API keys in the console."""

    list_display = ["name", "masked_key", "is_active", "created_at", "last_used_at"]
    exclude = ["key"]

    @admin.display(description="Key")
    def masked_key(self, obj):
        return f"{obj.prefix}***"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

Delete the dashboard, the skin, the link helpers and their tests:

```bash
git rm config/dashboard.py config/admin_links.py \
  templates/admin/dashboard.html templates/admin/base_site.html static/admin/css/ops.css \
  config/_tests/test_admin_links.py config/_tests/test_admin_site.py \
  config/_tests/test_dashboard.py config/_tests/test_dashboard_render.py \
  config/_tests/test_ops_css.py
```

In `apps/orchestration/_tests/test_admin.py`, delete `TestMonitoringAdminSite` and
`TestDashboardContext` (lines 21 to 138), then drop the imports ruff reports unused.

`apps/console/queries/fleet.py`: delete `url: str` from `NodeMetrics`, the
`url=reverse("console:node", args=[node.pk]),` line and the `reverse` import.

Fix what named the dashboard:
- `apps/console/queries/reports.py`: the docstring is the first line alone.
- `config/describing.py:5`: the docstring ends "so nothing upstream imports
  ``apps.console``." and loses the PR 11 exception.
- `apps/console/AGENTS.md:9`: the rule ends "never the reverse." and loses the exception.
- `config/_tests/test_settings.py:105`: drop `"admin/css/ops.css"` from the collected names.
- `config/_tests/test_no_inline_colour.py`:
  - `_scanned()` drops `ROOT / "config" / "dashboard.py"`.
  - `MUST_BE_SCANNED` drops `"templates/admin/dashboard.html"` and `"config/dashboard.py"`
    and adds `"templates/console/base.html"`.
  - The docstring opens "Colour lives in the console stylesheet." and names
    `static/console/console.css` where it named `static/admin/css/ops.css`.
  - The assertion message reads `inline colour belongs in console.css`.

```bash
uv run ruff check . --fix
grep -rn "config\.dashboard\|get_dashboard_context\|admin_links\|ops\.css\|SECTION_MAP\|base_site" \
  --include='*.py' --include='*.html' --include='*.css' apps config templates static
```

Expected: the grep prints only `apps/alerts/node_policy.py:12` and `:401`, which Task 8
rewords, and nothing else.

**Step 4: Run the tests to see them pass**

```bash
uv run pytest config/_tests apps/orchestration/_tests apps/console/_tests apps/alerts/_tests
```

Expected: PASS.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A config apps templates static
git commit -m "feat!: /admin/ is superuser break-glass, and the admin dashboard is gone"
```

---

### Task 6: Every model is editable, and no secret shows

**Files:**
- Modify: `apps/alerts/admin.py`, `apps/checkers/admin.py`, `apps/intelligence/admin.py`,
  `apps/notify/admin.py`, `apps/orchestration/admin.py`, `config/admin.py`
- Test: `config/_tests/test_admin.py`, `config/_tests/test_api_key_admin.py`,
  `apps/alerts/_tests/test_admin.py`, `apps/checkers/_tests/test_admin.py`,
  `apps/intelligence/_tests/test_admin.py`, `apps/notify/_tests/test_admin.py`,
  `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing tests**

Add to `config/_tests/test_admin.py`, with the new imports merged into its import block:

```python
from apps.console._tests.fixtures import (
    OPS_SLACK_WEBHOOK,
    PROVIDER_CONFIG,
    PROVIDER_SECRET,
    build_world,
)
from apps.intelligence.models import AnalysisRun, IntelligenceProvider
from apps.notify.models import NotificationChannel
from apps.orchestration.models import InboxItem, PipelineDefinition
from config.models import APIKey

ADD_REFUSED = {APIKey, IntelligenceProvider, NotificationChannel}
SECRET_FIELDS = {
    NotificationChannel: "config",
    IntelligenceProvider: "config",
    AnalysisRun: "provider_config",
    APIKey: "key",
}


def _request(rf, user):
    request = rf.get("/admin/")
    request.user = user
    return request


def test_a_superuser_can_change_and_delete_every_model(rf, admin_user):
    request = _request(rf, admin_user)
    for model, model_admin in admin.site._registry.items():
        assert model_admin.has_change_permission(request), model
        assert model_admin.has_delete_permission(request), model
        assert model_admin.has_add_permission(request) is (model not in ADD_REFUSED), model


def test_no_admin_form_carries_a_secret_field(rf, admin_user):
    request = _request(rf, admin_user)
    for model, field in SECRET_FIELDS.items():
        assert field not in admin.site._registry[model].get_form(request).base_fields, model


def test_the_inbox_proxy_is_not_registered():
    assert not admin.site.is_registered(InboxItem)


def _pages(model, pks):
    name = f"admin:{model._meta.app_label}_{model._meta.model_name}"
    yield reverse(f"{name}_changelist")
    for pk in pks:
        for page in ("change", "history", "delete"):
            yield reverse(f"{name}_{page}", args=[pk])


def test_no_admin_page_shows_a_stored_secret(admin_client):
    world = build_world()
    secrets = [
        OPS_SLACK_WEBHOOK,
        PROVIDER_CONFIG,
        PROVIDER_SECRET,
        *world.key_tokens,
        *APIKey.objects.values_list("key", flat=True),
    ]
    holders = {*SECRET_FIELDS, PipelineDefinition}
    urls = [
        url
        for model in admin.site._registry
        for url in _pages(
            model,
            model._default_manager.values_list("pk", flat=True)[
                : None if model in holders else 3
            ],
        )
    ]

    for url in urls:
        resp = admin_client.get(url)
        assert resp.status_code == 200, url
        body = resp.content.decode()
        for secret in secrets:
            assert secret not in body, (url, secret)
```

`PipelineDefinition` is a holder because its change page lists every channel in the
`channel` select.

In `config/_tests/test_api_key_admin.py`, `test_the_key_admin_is_stock_and_read_only`
becomes:

```python
@pytest.mark.django_db
def test_the_key_admin_edits_but_never_adds(admin_client, rf, admin_user):
    APIKey.objects.create(name="listed")
    model_admin = admin.site._registry[APIKey]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["key"]
    response = admin_client.get(reverse("admin:config_app_apikey_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


@pytest.mark.django_db
def test_a_key_edit_keeps_its_digest(admin_client):
    key = APIKey.objects.create(name="agent")
    digest = key.key
    url = reverse("admin:config_app_apikey_change", args=[key.pk])

    response = admin_client.post(
        url,
        {
            "name": "renamed",
            "is_active": "on",
            "allowed_endpoints": "[]",
            "last_used_at_0": "",
            "last_used_at_1": "",
        },
    )

    assert response.status_code == 302
    key.refresh_from_db()
    assert (key.name, key.key) == ("renamed", digest)
```

`apps/notify/_tests/test_admin.py` becomes:

```python
import pytest
from django.contrib import admin
from django.urls import reverse

from apps.notify.models import NotificationChannel

pytestmark = pytest.mark.django_db


def test_the_channel_admin_edits_but_never_adds(admin_client, rf, admin_user):
    NotificationChannel.objects.create(name="listed", driver="email", config={})
    model_admin = admin.site._registry[NotificationChannel]
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["config"]
    assert model_admin.readonly_fields == ["driver"]
    response = admin_client.get(reverse("admin:notify_notificationchannel_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_a_channel_edit_keeps_its_stored_settings(admin_client):
    stored = {"webhook_url": "https://hooks.slack.com/SECRETXYZ"}
    channel = NotificationChannel.objects.create(name="slack", driver="slack", config=stored)
    url = reverse("admin:notify_notificationchannel_change", args=[channel.pk])
    page = admin_client.get(url)
    assert page.status_code == 200
    assert "SECRETXYZ" not in page.content.decode()

    response = admin_client.post(url, {"name": "renamed", "is_active": "on", "description": ""})

    assert response.status_code == 302
    channel.refresh_from_db()
    assert (channel.name, channel.driver, channel.config) == ("renamed", "slack", stored)
```

`apps/intelligence/_tests/test_admin.py` becomes:

```python
import pytest
from django.contrib import admin
from django.urls import reverse

from apps.intelligence.models import AnalysisRun, IntelligenceProvider

pytestmark = pytest.mark.django_db


def _request(rf, user):
    request = rf.get("/")
    request.user = user
    return request


def test_the_provider_admin_edits_but_never_adds(admin_client, rf, admin_user):
    IntelligenceProvider.objects.create(name="listed", provider="claude")
    model_admin = admin.site._registry[IntelligenceProvider]
    request = _request(rf, admin_user)
    assert not model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["config"]
    assert model_admin.readonly_fields == ["provider"]
    response = admin_client.get(reverse("admin:intelligence_intelligenceprovider_changelist"))
    assert response.status_code == 200
    assert "listed" in response.content.decode()


def test_a_provider_edit_keeps_its_stored_settings(admin_client):
    stored = {"api_key": "sk-SECRETXYZ"}
    provider = IntelligenceProvider.objects.create(name="main", provider="claude", config=stored)
    url = reverse("admin:intelligence_intelligenceprovider_change", args=[provider.pk])
    page = admin_client.get(url)
    assert page.status_code == 200
    assert "SECRETXYZ" not in page.content.decode()

    response = admin_client.post(url, {"name": "renamed", "description": ""})

    assert response.status_code == 302
    provider.refresh_from_db()
    assert (provider.name, provider.provider, provider.config) == ("renamed", "claude", stored)


def test_the_analysis_admin_is_editable_and_hides_the_provider_config(
    admin_client, rf, admin_user
):
    run = AnalysisRun.objects.create(
        trace_id="t",
        pipeline_run_id="r",
        provider="openai",
        provider_config={"base_url": "https://llm.internal.example"},
    )
    model_admin = admin.site._registry[AnalysisRun]
    request = _request(rf, admin_user)
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert model_admin.exclude == ["provider_config"]
    assert model_admin.raw_id_fields == ["incident"]
    page = admin_client.get(reverse("admin:intelligence_analysisrun_change", args=[run.pk]))
    assert page.status_code == 200
    assert "llm.internal.example" not in page.content.decode()
```

`apps/checkers/_tests/test_admin.py`: rename the test
`test_the_admin_is_stock_and_editable`, flip the three permission asserts to true, and
add `assert admin.site._registry[CheckRun].raw_id_fields == ["alert"]` as its own test.

`apps/orchestration/_tests/test_admin.py`:
- Delete `TestInboxAdmin` and `TestInboxAdminQueryset`.
- `test_run_admins_are_stock_and_read_only` becomes:

```python
@pytest.mark.django_db
@pytest.mark.parametrize("model", [PipelineRun, StageExecution, PipelineDefinition])
def test_the_orchestration_admins_are_editable(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert "delete_selected" in model_admin.get_actions(request)


def test_the_inbox_is_not_registered():
    assert not admin.site.is_registered(InboxItem)


def test_run_foreign_keys_are_raw_ids():
    assert admin.site._registry[PipelineRun].raw_id_fields == ["node", "incident"]
    assert admin.site._registry[StageExecution].raw_id_fields == ["pipeline_run"]
```

- `test_the_route_admin_is_stock_and_read_only` keeps only its changelist assertions,
  renamed `test_the_route_changelist_lists_routes`.

`apps/alerts/_tests/test_admin.py`: keep `TestAdminQueryOptimization`. Replace
`TestIncidentAdminIsStock`, `TestAlertAdminIsStock` and `TestNodeAdminIsStock` with:

```python
@pytest.mark.django_db
@pytest.mark.parametrize("model", [Alert, Incident, AlertHistory, Node])
def test_the_alerts_admins_are_editable(model, rf, admin_user):
    model_admin = admin.site._registry[model]
    request = rf.get("/")
    request.user = admin_user
    assert model_admin.has_add_permission(request)
    assert model_admin.has_change_permission(request)
    assert model_admin.has_delete_permission(request)
    assert "delete_selected" in model_admin.get_actions(request)


def test_large_foreign_keys_are_raw_ids():
    assert admin.site._registry[Alert].raw_id_fields == ["node", "incident"]
    assert admin.site._registry[Incident].raw_id_fields == ["node"]
    assert admin.site._registry[AlertHistory].raw_id_fields == ["alert"]


class TestIncidentAdminSearch(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.incident = Incident.objects.create(subject="HighLatency", instance="db-1")

    def test_search_matches_subject_and_instance(self):
        other = Incident.objects.create(subject="DiskFull", instance="web-1")
        for term in ("HighLatency", "db-1"):
            with self.subTest(term=term):
                html = self.client.get("/admin/alerts/incident/", {"q": term}).content.decode()
                self.assertIn(f"/admin/alerts/incident/{self.incident.pk}/change/", html)
                self.assertNotIn(f"/admin/alerts/incident/{other.pk}/change/", html)


@pytest.mark.django_db
def test_the_admin_policy_page_is_gone(admin_client):
    assert admin_client.get("/admin/policy/").status_code == 404
```

Add `import pytest` and drop the imports ruff reports unused, such as `RequestFactory` and
`timezone`.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest config/_tests/test_admin.py config/_tests/test_api_key_admin.py apps/alerts/_tests/test_admin.py apps/checkers/_tests/test_admin.py apps/intelligence/_tests/test_admin.py apps/notify/_tests/test_admin.py apps/orchestration/_tests/test_admin.py
```

Expected: FAIL on every permission assert, on `readonly_fields` and `raw_id_fields`, and on
the inbox registration. `test_no_admin_page_shows_a_stored_secret` fails too, because a
read-only admin answers its delete page with a 403. `test_no_admin_form_carries_a_secret_field`
already passes, since the excludes are in place, and holds for the edits below.

**Step 3: Implement**

`apps/alerts/admin.py`:

```python
"""Break-glass admin for alerts models. Operators work in the console."""

from django.contrib import admin

from apps.alerts.models import Alert, AlertHistory, Incident, Node


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ["name", "severity", "status", "source", "node", "incident", "received_at"]
    list_filter = ["status", "severity", "source"]
    search_fields = ["name", "fingerprint", "trace_id"]
    list_select_related = ["node", "incident"]
    raw_id_fields = ["node", "incident"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["subject", "instance", "severity", "status", "created_at", "resolved_at"]
    list_filter = ["status", "severity"]
    search_fields = ["subject", "instance", "summary"]
    list_select_related = ["node", "pipeline"]
    raw_id_fields = ["node"]
    date_hierarchy = "created_at"


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    list_display = ["alert", "event", "old_status", "new_status", "created_at"]
    list_filter = ["event"]
    search_fields = ["alert__name"]
    list_select_related = ["alert"]
    raw_id_fields = ["alert"]


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ["instance_id", "hostname", "last_source", "first_seen", "last_seen"]
    search_fields = ["instance_id", "hostname"]
```

`apps/checkers/admin.py`:

```python
"""Break-glass admin for checkers models. Operators work in the console."""

from django.contrib import admin

from apps.checkers.models import CheckRun, PreflightRun


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = ["checker_name", "hostname", "status", "executed_at"]
    raw_id_fields = ["alert"]


@admin.register(PreflightRun)
class PreflightRunAdmin(admin.ModelAdmin):
    list_display = ["created_at", "overall_status", "passed", "warnings", "errors"]
```

`apps/intelligence/admin.py`:

```python
"""Break-glass admin for intelligence models. Operators work in the console."""

from django.contrib import admin

from apps.intelligence.models import AnalysisRun, IntelligenceProvider


@admin.register(IntelligenceProvider)
class IntelligenceProviderAdmin(admin.ModelAdmin):
    """Its ``config`` holds secrets, so only the console adds a provider or sets it."""

    list_display = ["name", "provider", "is_active", "updated_at"]
    exclude = ["config"]
    readonly_fields = ["provider"]

    def has_add_permission(self, request):
        return False


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ["created_at", "provider", "status", "total_tokens"]
    exclude = ["provider_config"]
    raw_id_fields = ["incident"]
```

`apps/notify/admin.py`:

```python
"""Break-glass admin for notify models. Operators work in the console."""

from django.contrib import admin

from apps.notify.models import NotificationChannel


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    """Its ``config`` holds secrets, so only the console adds a channel or sets it."""

    list_display = ["name", "driver", "is_active", "updated_at"]
    exclude = ["config"]
    readonly_fields = ["driver"]

    def has_add_permission(self, request):
        return False
```

`apps/orchestration/admin.py`:

```python
"""Break-glass admin for orchestration models. Operators work in the console."""

from django.contrib import admin

from apps.orchestration.models import PipelineDefinition, PipelineRun, StageExecution


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = [
        "run_id",
        "status",
        "origin",
        "node",
        "incident",
        "current_stage",
        "total_attempts",
        "created_at",
    ]
    list_filter = ["status", "origin", "node", "source", "current_stage", "environment"]
    search_fields = ["run_id", "trace_id", "alert_fingerprint"]
    list_select_related = ["node", "incident"]
    raw_id_fields = ["node", "incident"]


@admin.register(StageExecution)
class StageExecutionAdmin(admin.ModelAdmin):
    list_display = ["pipeline_run", "stage", "status", "attempt", "duration_ms", "started_at"]
    list_filter = ["stage", "status"]
    search_fields = ["pipeline_run__run_id", "pipeline_run__trace_id", "idempotency_key"]
    list_select_related = ["pipeline_run"]
    raw_id_fields = ["pipeline_run"]


@admin.register(PipelineDefinition)
class PipelineDefinitionAdmin(admin.ModelAdmin):
    list_display = ["name", "priority", "is_active", "updated_at"]
```

`config/admin.py`: `APIKeyAdmin` keeps `list_display`, `exclude` and `masked_key`. Its
docstring becomes `"""The raw token is shown once, by the console's create page, so only
the console adds a key."""`. It keeps `has_add_permission` returning False, with signature
`(self, request)`, and drops the change and delete overrides.

**Step 4: Run the tests to see them pass**

```bash
uv run pytest config/_tests apps/alerts/_tests apps/checkers/_tests apps/intelligence/_tests apps/notify/_tests apps/orchestration/_tests
```

Expected: PASS. If the secret test names a page, that page is a real leak. Exclude the
field on that admin rather than loosening the test.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add -A config apps
git commit -m "feat!: every model is editable in the break-glass admin, secrets stay hidden"
```

---

### Task 7: Hints name console pages

**Files:**
- Modify: `apps/checkers/checks.py:442,457`, `apps/checkers/preflight/checks.py:473,505,521`,
  `config/management/commands/setup_cluster.py:191,221`
- Test: `apps/checkers/_tests/test_checks.py`, `apps/checkers/_tests/preflight/test_checks.py`,
  `config/_tests/commands/test_setup_cluster.py`

**Step 1: Write the failing tests**

`apps/checkers/_tests/test_checks.py`:
- `test_notification_channels_warns_when_none_active` adds
  `self.assertIn("/console/channels/new/", errors[0].hint)`.
- `test_notification_channels_warns_on_empty_config` adds
  `self.assertIn("/console/channels/", errors[0].hint)`.

`apps/checkers/_tests/preflight/test_checks.py`, in `CheckPipelineStateTests`:
- `test_no_active_definitions_warns_that_routing_is_dead` adds
  `self.assertIn("/console/routes/new/", offender[0].hint)`.
- `test_a_lane_that_claims_to_deliver_but_cannot_is_an_error` adds
  `self.assertIn("/console/routes/", errors[0].hint)`.
- `test_a_hub_with_no_channel_and_no_delivering_lane_is_not_a_problem` adds
  `self.assertTrue(any("/console/channels/new/" in r.hint for r in infos))`.

`config/_tests/commands/test_setup_cluster.py`:
- `test_hub_no_notify_warns` adds `self.assertIn("/console/channels/new/", out)`.
- The test asserting `"nothing bound"` adds `self.assertIn("/console/routes/", out)`.

Each of those tests also asserts `"Django" not in` the same hint or output.

**Step 2: Run the tests to see them fail**

```bash
uv run pytest apps/checkers/_tests/test_checks.py apps/checkers/_tests/preflight/test_checks.py config/_tests/commands/test_setup_cluster.py
```

Expected: FAIL on the seven new assertions.

**Step 3: Implement**

`apps/checkers/checks.py`:
- `:442`: `"Create a notification channel at /console/channels/new/"` followed by the
  existing `" or run 'python manage.py setup_cluster'."`.
- `:456-457`: `f"Configure {ch['driver']} settings for channel"` then
  `f" '{ch['name']}' at /console/channels/."`.

`apps/checkers/preflight/checks.py`:
- `:473-474`: `"0012 seeds a catch-all lane. Re-add one at /console/routes/new/ "` then
  `"with no conditions."`.
- `:505-506`: `"is not registered. Fix the route at /console/routes/, or drop notify "` then
  `"from its stages if this hub only records."`.
- `:521-522`: `"Add a channel at /console/channels/new/ and point a route at it if this "`
  then `"hub should notify."`.

`config/management/commands/setup_cluster.py`:
- `:191-192`: the two literals after "nothing bound. " become
  `"Pick one per route at /console/routes/."`.
- `:221`: `"Add one at /console/channels/new/ or re-run with "`.

```bash
grep -rn -i "django admin" --include='*.py' apps config | grep -v "_tests"
```

Expected: nothing.

**Step 4: Run the tests to see them pass**

Same command as Step 2. Expected: PASS.

**Step 5: Commit**

```bash
uv run black . && uv run ruff check . --fix
git add apps/checkers config
git commit -m "fix: hints send operators to console pages, not the Django admin"
```

---

### Task 8: Docs

No tests. Every edit below drops the read-only admin story and says the console is the
operations surface and `/admin/` is superuser break-glass. Wherever a doc says what an
admin edit does, it says this: an admin edit writes the raw row, skips the console's
version and digest checks and `IncidentManager.transition`, writes no `AlertHistory`, and
enqueues no run.

**Repo `AGENTS.md`:**
- `:151`: "`admin.py`: a plain registration per model, for break-glass use at `/admin/`."
- `:159`, the console row: "Operator console at `/console/`, with its own login at
  `/console/login/`. Server-rendered, active staff only. The only operator surface:
  `/admin/` is superuser break-glass. A consumer: not a stage, not a producer."
- `:318`, convention 2: "Every app under `apps/<app_name>/` must include `views/`
  (package), `_tests/` (mirrors source layout) and `AGENTS.md`. An app with models
  registers each in a plain `admin.py`."
- `:319`, convention 3 is rewritten:

  > 3. **The console is the operations surface.** Operators read and act on everything at
  > `/console/`. It signs in at `/console/login/` and admits active staff, and each page
  > needs the model's view or change permission. `/admin/` is break-glass: stock Django,
  > open only to active superusers through `MonitoringAdminSite.has_permission`
  > (`config/admin.py`). Each model has a plain, editable `ModelAdmin`. Secret fields stay
  > excluded, so only the console changes them: `NotificationChannel.config`,
  > `IntelligenceProvider.config`, `AnalysisRun.provider_config` and `APIKey.key`. Channels,
  > providers and API keys are added only in the console. An admin edit writes the raw
  > row. It skips the console's version and digest checks and the incident transitions,
  > and it enqueues no run. Readiness lives in `config/readiness.py`: `build_readiness()`
  > and `keyed_readiness()`, which the console Overview renders. Its nodes entry counts
  > peers only. This instance's own `Node` row, matched on
  > `apps.alerts.identity.local_instance_id()` and never on `last_source`, is left out of
  > the freshness totals and reported as a self-check age, because a hub checked by hand
  > is not a quiet agent. A peerless install reads `info` "Standalone", not amber.

- `:333`, convention 14: "`config/readiness.py` describes readiness through
  `keyed_readiness()`".

**App `AGENTS.md` files.** Each "Django Admin expectations" heading becomes "Admin", and
the "Each app must provide an **extensive** `admin.py`" sentence goes.
- `apps/alerts/AGENTS.md:270-312`: the four admins are plain and editable, with raw-id
  foreign keys. An edit writes the raw row as above, and a node edit also bumps
  `last_seen`. Under "Node detail page", drop "`NodeAdmin` is stock and read-only." The
  local-node freshness bullet names `config/readiness.py`. Under "Node policy form", drop
  "the same way one guards `config/admin.py`'s `SECTION_MAP`".
- `apps/checkers/AGENTS.md:58`: the two admins are plain and editable. Check and preflight
  runs are audit rows, so editing one changes history.
- `apps/intelligence/AGENTS.md:79`: the provider admin excludes `config`, refuses add and
  shows `provider` read-only. The analysis admin excludes `provider_config`.
- `apps/notify/AGENTS.md:112`: the channel admin excludes `config`, refuses add and shows
  `driver` read-only. Deliveries are read on the console run page.
- `apps/orchestration/AGENTS.md:289`: `config/readiness.py:build_readiness`. `:326`: the
  run, stage and route admins are plain and editable. `InboxAdmin` is gone, and the inbox
  is read and drained at `/console/inbox/`. A route edit there skips the `version` bump, so
  a console editor opened earlier won't see it as stale.
- `apps/console/AGENTS.md`:
  - `:13`: "Action views call the functions the owning app exposes, such as
    `IncidentManager`."
  - `:15-19`: "Every view subclasses `ConsoleView`, except `ConsoleLoginView` and Django's
    `LogoutView`. An anonymous page request is redirected to `/console/login/` through
    `LOGIN_URL`." Add a bullet: the login form (`forms.py`) admits active staff only, and
    every refusal reads the same so it names no account. Logout is POST-only and lands on
    the login.
  - `:53`: `config.readiness.keyed_readiness()`.
  - `:189`: "Their admins are break-glass only."
  - `:252-253`: "`admin_url()` builds the header's Admin link, shown only to superusers."
  - `:281-282`: drop the two sentences about the admin's incident buttons.

**READMEs:**
- `apps/alerts/README.md:51-74`: the "Django Admin" section, through its note, becomes
  "Admin": operators read and act on alerts and incidents at `/console/alerts/` and
  `/console/incidents/`. `/admin/alerts/` is superuser break-glass, and an edit there
  writes the raw row.
- `apps/checkers/README.md:63-70`: "Admin". Keep the console paragraph. The admins are
  plain and editable for superusers. Rewrite the note: check runs are audit rows written
  by the checks, and editing one in the admin changes history.
- `apps/intelligence/README.md:261-269`: steps 1 to 3 become "Add a provider at
  `/console/providers/new/` and turn it on. Only one can be active at a time, enforced by
  `UniqueConstraint`." Steps 4 to 6 stay.
- `apps/notify/README.md:155`: "The `/admin/notify/` channel admin is superuser
  break-glass and never shows stored settings." `:297`: "the console Overview's readiness
  panel".
- `apps/orchestration/README.md:424`: "The orchestration admin at `/admin/orchestration/`
  is superuser break-glass."

**`docs/`:**
- `Architecture.md:123`: readiness faults come from `config/readiness.py`. "The console and
  notifications render these descriptions".
- `Architecture.md:127`: "Stage behavior is controlled through routes in the console, not
  environment variables."
- `Architecture.md:195-206`: "### Django Admin" becomes "### Break-glass admin": one
  paragraph saying operators sign in at `/console/login/` and work in the console. Then
  `/admin/` is stock Django for active superusers, every model editable, the four secret
  fields excluded, and the raw-row warning. Drop the table.
- `Architecture.md:368`: "managed through the console."
- `Security.md:120`: drop "The Django admin lists them read-only."
- `Security.md:152`: add "`/console/*` is not an API path. It uses Django session auth and
  signs in at `/console/login/`."
- `Security.md:161`: "for safe display (`{prefix}***` in the console key list and the admin
  changelist)".
- `Security.md:544-552`, "Admin Interface" becomes "Console and admin":
  - The console signs in active staff at `/console/login/`. Each page needs the model's
    view or change permission, and each action its change or add permission.
  - `/admin/` admits only active superusers. Use it to manage users, groups and their
    permissions, and as break-glass for a raw row.
  - Secret fields never render in the admin. A test renders every admin page against
    stored secrets.
  - An admin edit skips the console's checks and writes no audit trail beyond Django's
    `LogEntry`.
- `Security.md` ISO table:
  - A.5.15: "Django session auth on `/console/` (active staff, per-model permissions) and
    `/admin/` (active superusers)".
  - A.8.2: "`/admin/` admits only active superusers. Console actions need staff plus the
    model's change permission".
  - A.8.3: "admin and console outside the webhook surface".
  - A.8.26: "the console renders through autoescaped templates under a strict CSP".
- `Deployment.md:583`: "searchable in the console alert list (`/console/alerts/`)".
- `Deployment.md:676`: "Browse them at `/console/nodes/`."
- `Setup-Guide.md:508`: drop "The Django admin lists providers read-only."

**Comments in code:**
- `apps/alerts/node_policy.py:12`: drop ", matching how ``config.SECTION_MAP`` completeness
  is guarded" and end the sentence at `test_node_policy.py``.
- `apps/alerts/node_policy.py:400-402`: "Importing it here would pull the sparkline
  renderer and ``config.readiness`` into the policy editor for a question that needs names
  only."

**Check:**

```bash
grep -rn -i "django admin\|read-only admin\|stock and read-only\|config/dashboard\|admin_badges\|admin_links\|SECTION_MAP\|ops\.css" \
  AGENTS.md README.md docs/*.md apps/*/AGENTS.md apps/*/README.md apps config
```

Expected: nothing.

**Commit:**

```bash
git add AGENTS.md docs/*.md apps/*/AGENTS.md apps/*/README.md apps/alerts/node_policy.py
git commit -m "docs: the console is the operations surface, /admin/ is break-glass"
```

---

### Task 9: Verify

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report --show-missing --include="config/admin.py,config/readiness.py,config/tones.py,apps/console/forms.py,apps/console/views/login.py,apps/console/views/base.py,apps/console/queries/fleet.py,apps/console/queries/reports.py,apps/*/admin.py"
uv run python manage.py check
uv run bandit -r apps/ config/ -c pyproject.toml
```

Expected: everything green, and every listed file at 100%.

By hand, with `uv run python manage.py runserver`:
- `/console/` signed out lands on `/console/login/?next=/console/`. A staff login returns
  there.
- A non-staff login reads the refusal. Sign out lands on the login.
- The header shows Admin only to a superuser.
- `/admin/` as plain staff sends you to the admin login. As a superuser it's the stock
  index, and a channel's change page has no config field.

{% endraw %}
