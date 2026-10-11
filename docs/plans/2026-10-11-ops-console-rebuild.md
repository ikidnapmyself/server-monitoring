---
title: "Ops Console Rebuild Implementation"
parent: Plans
---

{% raw %}

# Ops Console Rebuild Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship PR 1 of the ops console rebuild: a new `apps/console` app at `/console/` with auth, CSP, a vendored and guarded htmx, the CSS token layer and a working Overview page.

**Architecture:** Server-rendered Django templates. Views are thin over `apps/console/queries/`, which returns frozen dataclasses. One URL serves the full page and, on an htmx request, only the partial. `django.contrib.admin` is untouched.

**Tech Stack:** Django 5.2, pytest-django, one vendored `htmx.min.js`, hand-written CSS and JS. No npm, no build step.

**Design:** `docs/plans/2026-10-11-ops-console-rebuild-design.md`

---

## Scope of this plan

The design ships as six PRs. This plan details PR 1 step by step and outlines PRs 2 to 6
at the end. Each later PR gets its own detailed plan when its turn comes, because its
code depends on shapes PR 1 establishes. Planning all six at step level now would
produce a plan far larger than the code, which `AGENTS.md` names as a scope warning.

## Ground rules for the executor

- Branch: `git switch -c feat/console-skeleton docs/ops-console-rebuild-design`. Never
  commit to `main`. Don't use git worktrees.
- Every commit runs the full pytest suite and mypy through pre-commit. A commit takes
  several minutes. Don't pass `--no-verify`.
- Absolute imports only. Line length 100. No comments that restate the code.
- No inline `<script>`, no `<style>`, no `style=` and no `on*=` attributes in any console
  template. The CSP blocks them and a guard test fails on them.
- No hex colours outside `static/console/console.css`.
- Run a single test file with `uv run pytest <path> -v`.
- `apps/console` has no models, so it has no `admin.py` and no migrations. That is a
  deliberate exception to the app layout rule and `apps/console/AGENTS.md` says so.

---

### Task 1: App scaffold, routing and the staff check

**Files:**
- Create: `apps/console/__init__.py` (empty)
- Create: `apps/console/apps.py`
- Create: `apps/console/urls.py`
- Create: `apps/console/views/__init__.py`
- Create: `apps/console/views/base.py`
- Create: `apps/console/views/overview.py`
- Create: `templates/console/overview.html`
- Create: `templates/console/partials/_overview.html`
- Create: `apps/console/_tests/__init__.py` (empty)
- Create: `apps/console/_tests/views/__init__.py` (empty)
- Create: `apps/console/_tests/views/test_overview.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`)
- Modify: `config/urls.py`
- Modify: `pyproject.toml` (bandit `exclude_dirs`)

**Step 1: Write the failing tests**

`apps/console/_tests/views/test_overview.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user("op", password="x", is_staff=True)
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_anonymous_is_sent_to_the_admin_login(client):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 302
    assert resp["Location"] == f"{reverse('admin:login')}?next=/console/"


@pytest.mark.django_db
def test_non_staff_is_refused(client):
    user = get_user_model().objects.create_user("visitor", password="x")
    client.force_login(user)

    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 403


@pytest.mark.django_db
def test_staff_gets_the_page(client, staff):
    resp = client.get(reverse("console:overview"))

    assert resp.status_code == 200
    assert "<html" in resp.content.decode()


@pytest.mark.django_db
def test_root_redirects_to_the_console(client):
    resp = client.get("/")

    assert resp.status_code == 302
    assert resp["Location"] == reverse("console:overview")
```

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests/views/test_overview.py -v`
Expected: 4 errors, `NoReverseMatch: 'console' is not a registered namespace`.

**Step 3: Write the implementation**

`apps/console/apps.py`:

```python
from django.apps import AppConfig


class ConsoleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.console"
```

`apps/console/views/base.py`:

```python
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.urls import reverse
from django.views.generic import TemplateView


class ConsoleView(TemplateView):
    """Base for every console page. Staff only."""

    def dispatch(self, request, *args, **kwargs):
        return self._refusal(request) or super().dispatch(request, *args, **kwargs)

    def _refusal(self, request):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("admin:login"))
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required.")
        return None
```

`apps/console/views/overview.py`:

```python
from apps.console.views.base import ConsoleView


class OverviewView(ConsoleView):
    template_name = "console/overview.html"
```

`apps/console/views/__init__.py`:

```python
from apps.console.views.overview import OverviewView

__all__ = ["OverviewView"]
```

`apps/console/urls.py`:

```python
from django.urls import path

from apps.console.views import OverviewView

app_name = "console"

urlpatterns = [
    path("", OverviewView.as_view(), name="overview"),
]
```

`templates/console/overview.html` (placeholder, replaced in Task 7):

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Console</title></head>
<body>{% include "console/partials/_overview.html" %}</body>
</html>
```

`templates/console/partials/_overview.html` (placeholder, replaced in Task 7):

```html
<p>Overview</p>
```

In `config/settings.py`, add to `INSTALLED_APPS` after the orchestration entry:

```python
    "apps.console.apps.ConsoleConfig",
```

In `config/urls.py`, change the import and the list:

```python
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="console:overview"), name="root"),
    path("admin/", admin.site.urls),
    path("console/", include("apps.console.urls")),
    path("alerts/", include("apps.alerts.urls")),
    path("notify/", include("apps.notify.urls")),
    path("intelligence/", include("apps.intelligence.urls")),
    path("orchestration/", include("apps.orchestration.urls")),
]
```

In `pyproject.toml`, add `"apps/console/_tests"` to bandit's `exclude_dirs` list, beside
the other `_tests` entries.

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests/views/test_overview.py -v`
Expected: 4 passed.

**Step 5: Commit**

```bash
git add apps/console templates/console config/settings.py config/urls.py pyproject.toml
git commit -m "feat(console): add the console app with a staff-only overview route"
```

---

### Task 2: CSP header and htmx-aware responses

**Files:**
- Modify: `apps/console/views/base.py`
- Modify: `apps/console/views/overview.py`
- Modify: `apps/console/_tests/views/test_overview.py`

**Step 1: Write the failing tests**

Append to `apps/console/_tests/views/test_overview.py`:

```python
from apps.console.views.base import CSP

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.mark.django_db
def test_every_response_carries_the_csp(client, staff):
    resp = client.get(reverse("console:overview"))

    assert resp["Content-Security-Policy"] == CSP
    assert "script-src 'self'" in CSP
    assert "style-src 'self'" in CSP
    assert "unsafe" not in CSP


@pytest.mark.django_db
def test_refusals_carry_the_csp_too(client):
    resp = client.get(reverse("console:overview"))

    assert resp["Content-Security-Policy"] == CSP


@pytest.mark.django_db
def test_htmx_request_gets_only_the_partial(client, staff):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 200
    assert "<html" not in resp.content.decode()
    assert "HX-Request" in resp["Vary"]


@pytest.mark.django_db
def test_anonymous_htmx_request_is_told_to_do_a_full_redirect(client):
    resp = client.get(reverse("console:overview"), **HTMX)

    assert resp.status_code == 204
    assert resp["HX-Redirect"] == f"{reverse('admin:login')}?next=/console/"
```

Move the `from apps.console.views.base import CSP` line up with the other imports.

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests/views/test_overview.py -v`
Expected: collection error, `ImportError: cannot import name 'CSP'`.

**Step 3: Write the implementation**

Replace `apps/console/views/base.py`:

```python
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.generic import TemplateView

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class ConsoleView(TemplateView):
    """Base for every console page. Staff only, CSP on every response.

    One URL serves two sizes: the full page, and only ``partial_template_name``
    when htmx asks. Subclasses must set both template names.
    """

    partial_template_name: str

    def dispatch(self, request, *args, **kwargs):
        response = self._refusal(request) or super().dispatch(request, *args, **kwargs)
        response["Content-Security-Policy"] = CSP
        patch_vary_headers(response, ["HX-Request"])
        return response

    @property
    def is_htmx(self) -> bool:
        return self.request.headers.get("HX-Request") == "true"

    def get_template_names(self) -> list[str]:
        if self.is_htmx:
            return [self.partial_template_name]
        return [self.template_name]

    def _refusal(self, request):
        if not request.user.is_authenticated:
            redirect = redirect_to_login(request.get_full_path(), reverse("admin:login"))
            if not self.is_htmx:
                return redirect
            # htmx would swap a followed redirect's login form into the page.
            response = HttpResponse(status=204)
            response["HX-Redirect"] = redirect.url
            return response
        if not request.user.is_staff:
            return HttpResponseForbidden("Staff access required.")
        return None
```

In `apps/console/views/overview.py`, add the partial name:

```python
class OverviewView(ConsoleView):
    template_name = "console/overview.html"
    partial_template_name = "console/partials/_overview.html"
```

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests/views/test_overview.py -v`
Expected: 8 passed.

**Step 5: Commit**

```bash
git add apps/console
git commit -m "feat(console): send a strict CSP and serve partials to htmx"
```

---

### Task 3: Vendor htmx and guard the file

**Files:**
- Create: `static/console/vendor/htmx.min.js`
- Create: `static/console/vendor/VENDOR.md`
- Create: `apps/console/_tests/test_vendor.py`

**Step 1: Write the failing tests**

`apps/console/_tests/test_vendor.py`:

```python
"""The vendor directory holds exactly what VENDOR.md says, byte for byte.

There is no package manager watching these files, so this is the only thing
that notices a swapped file or a second library added without review.
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings

VENDOR = Path(settings.BASE_DIR) / "static" / "console" / "vendor"
MANIFEST = VENDOR / "VENDOR.md"

ROW = re.compile(r"^\| `([^`]+)` \| ([^| ]+) \| (https://[^| ]+) \| `([0-9a-f]{64})` \|$", re.M)


def _rows() -> dict[str, str]:
    return {name: sha for name, _version, _url, sha in ROW.findall(MANIFEST.read_text())}


def test_manifest_lists_htmx():
    assert "htmx.min.js" in _rows()


def test_every_vendored_file_matches_its_recorded_hash():
    for name, recorded in _rows().items():
        actual = hashlib.sha256((VENDOR / name).read_bytes()).hexdigest()
        assert actual == recorded, f"{name} does not match VENDOR.md"


def test_vendor_directory_holds_only_what_the_manifest_lists():
    on_disk = {path.name for path in VENDOR.iterdir()}

    assert on_disk == set(_rows()) | {MANIFEST.name}
```

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests/test_vendor.py -v`
Expected: 3 failed, `FileNotFoundError` on `VENDOR.md`.

**Step 3: Fetch htmx and record it**

Pick the newest 2.0.x release listed at `https://github.com/bigskysoftware/htmx/releases`.
Set it as `V` below. Fetch from two independent CDNs and confirm they agree before
trusting either.

```bash
V=2.0.x   # replace with the real version
mkdir -p static/console/vendor
curl -sSfL --max-time 30 "https://unpkg.com/htmx.org@${V}/dist/htmx.min.js" \
  -o static/console/vendor/htmx.min.js
curl -sSfL --max-time 30 "https://cdn.jsdelivr.net/npm/htmx.org@${V}/dist/htmx.min.js" \
  | shasum -a 256
shasum -a 256 static/console/vendor/htmx.min.js
```

Expected: both `shasum` lines print the same 64-character hash. If they differ, stop and
report it. Don't commit the file.

`static/console/vendor/VENDOR.md`, with the real version and hash filled in:

```markdown
# Vendored frontend files

The only third-party frontend code in this repository. There is no npm and no
build step. `apps/console/_tests/test_vendor.py` fails if a file here changes
or a file appears that this table does not list.

| File | Version | Source | SHA-256 |
|---|---|---|---|
| `htmx.min.js` | 2.0.x | https://unpkg.com/htmx.org@2.0.x/dist/htmx.min.js | `<64 hex chars>` |

## Upgrading

1. Read the upstream changelog for every version between the pinned one and the new one.
2. Fetch the new file from two CDNs and confirm the hashes agree.
3. Replace the file, update the row above, run `uv run pytest apps/console`.
```

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests/test_vendor.py -v`
Expected: 3 passed.

**Step 5: Commit**

```bash
git add static/console/vendor apps/console/_tests/test_vendor.py
git commit -m "feat(console): vendor htmx with a recorded hash and a guard test"
```

---

### Task 4: Scheduled advisory check for the vendored file

`pip-audit` can't see a vendored JS file. This workflow asks OSV whether the pinned
version of the `htmx.org` package has a known vulnerability. OSV aggregates GitHub
security advisories and matches on the exact version, which is more precise than
reading the repository's advisory list. It needs no npm.

**Files:**
- Create: `.github/workflows/vendor-advisories.yml`

**Step 1: Write the workflow**

```yaml
name: Vendor advisories

on:
  schedule:
    - cron: "17 6 * * 1"
  workflow_dispatch:
  pull_request:
    paths:
      - "static/console/vendor/**"
      - ".github/workflows/vendor-advisories.yml"

permissions:
  contents: read

jobs:
  htmx:
    name: htmx advisories
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Query OSV for the pinned htmx version
        run: |
          set -euo pipefail
          version=$(awk -F'|' '/`htmx.min.js`/ {gsub(/ /, "", $3); print $3}' \
            static/console/vendor/VENDOR.md)
          test -n "$version"
          echo "Pinned htmx version: $version"
          body=$(curl -sSf --max-time 30 --retry 3 -X POST https://api.osv.dev/v1/query \
            -d "{\"package\":{\"name\":\"htmx.org\",\"ecosystem\":\"npm\"},\"version\":\"$version\"}")
          count=$(echo "$body" | jq '.vulns // [] | length')
          if [ "$count" -gt 0 ]; then
            echo "$body" | jq -r '.vulns[] | "\(.id)  \(.summary // "no summary")"'
            exit 1
          fi
          echo "No known advisories."
```

**Step 2: Dry-run the shell locally**

Run the body of the `run:` block in a terminal from the repo root.
Expected: `Pinned htmx version: <V>` then `No known advisories.`

Then prove the failure path works by querying a version with a known advisory:

```bash
curl -sSf --max-time 30 -X POST https://api.osv.dev/v1/query \
  -d '{"package":{"name":"htmx.org","ecosystem":"npm"},"version":"1.9.0"}' \
  | jq '.vulns // [] | length'
```

Expected: a number. If it prints `0`, the failure path is unproven. Say so in the PR
description, don't claim it was verified.

**Step 3: Commit**

```bash
git add .github/workflows/vendor-advisories.yml
git commit -m "ci: check the vendored htmx version against OSV weekly"
```

---

### Task 5: The CSS token layer

**Files:**
- Create: `static/console/console.css`
- Create: `apps/console/_tests/test_css.py`

**Step 1: Write the failing tests**

`apps/console/_tests/test_css.py`:

```python
"""Contrast guard on console.css. A palette tweak must not drop text below AA."""

import re
from pathlib import Path

import pytest
from django.conf import settings

CSS = Path(settings.BASE_DIR) / "static" / "console" / "console.css"
DARK_MARKER = "@media (prefers-color-scheme: dark)"
TONES = ("critical", "warning", "info", "ok", "muted")
MIN_CONTRAST = 4.5


def _tokens(theme: str) -> dict[str, str]:
    light, dark = CSS.read_text().split(DARK_MARKER)
    block = dark.split("}")[0] if theme == "dark" else light.split("}")[0]
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", block))


def _luminance(hex_colour: str) -> float:
    parts = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(fg: str, bg: str) -> float:
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


PAIRS = [
    ("--text", "--bg"),
    ("--text", "--surface"),
    ("--text-muted", "--bg"),
    ("--text-muted", "--surface"),
    ("--accent", "--bg"),
    *[(f"--tone-{tone}-fg", f"--tone-{tone}-bg") for tone in TONES],
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_text_pairs_meet_aa(theme, fg, bg):
    tokens = _tokens(theme)

    assert _contrast(tokens[fg], tokens[bg]) >= MIN_CONTRAST


def test_both_themes_define_the_same_tokens():
    assert _tokens("light").keys() == _tokens("dark").keys()
```

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests/test_css.py -v`
Expected: 21 failed, `FileNotFoundError`.

**Step 3: Write the stylesheet**

`static/console/console.css`. The colour tokens come first, light then dark, because
the test reads the first rule on each side of the dark marker.

```css
:root {
  --bg: #ffffff;
  --surface: #f5f5f7;
  --border: #d2d2d7;
  --text: #1d1d1f;
  --text-muted: #6e6e73;
  --accent: #0057b8;
  --tone-critical-fg: #a1130b;
  --tone-critical-bg: #fde8e6;
  --tone-warning-fg: #7a4b00;
  --tone-warning-bg: #fff1d6;
  --tone-info-fg: #0b4f9e;
  --tone-info-bg: #e3effd;
  --tone-ok-fg: #116329;
  --tone-ok-bg: #dff5e3;
  --tone-muted-fg: #515154;
  --tone-muted-bg: #ececee;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1c1c1e;
    --surface: #2c2c2e;
    --border: #48484a;
    --text: #f5f5f7;
    --text-muted: #a1a1a6;
    --accent: #6cb4ff;
    --tone-critical-fg: #ffb4ab;
    --tone-critical-bg: #4a1512;
    --tone-warning-fg: #ffd58a;
    --tone-warning-bg: #45300a;
    --tone-info-fg: #a9cdff;
    --tone-info-bg: #0f2f57;
    --tone-ok-fg: #9ee6ae;
    --tone-ok-bg: #12381c;
    --tone-muted-fg: #c7c7cc;
    --tone-muted-bg: #3a3a3c;
  }
}

:root {
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-6: 24px;
  --space-8: 32px;
  --radius: 10px;
  --shadow: 0 1px 2px rgb(0 0 0 / 8%);
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  color-scheme: light dark;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  display: grid;
  grid-template-columns: 200px 1fr;
  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 var(--font);
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.c-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-6) var(--space-4);
  background: var(--surface);
  border-right: 1px solid var(--border);
}
.c-side__brand { margin: 0 0 var(--space-4); font-size: 13px; font-weight: 600; color: var(--text-muted); }
.c-side a { padding: var(--space-2) var(--space-3); border-radius: var(--radius); color: var(--text); }
.c-side a[aria-current="page"] { background: var(--bg); box-shadow: var(--shadow); font-weight: 600; }
.c-side__foot { margin-top: auto; display: flex; flex-direction: column; gap: var(--space-1); }
.c-side button {
  padding: var(--space-2) var(--space-3);
  border: 0;
  background: none;
  color: var(--text-muted);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.c-main { max-width: 1200px; width: 100%; padding: var(--space-8); }
.c-headline { margin: 0 0 var(--space-2); font-size: 28px; font-weight: 600; letter-spacing: -0.01em; }
.c-muted { color: var(--text-muted); }
.c-section { margin-top: var(--space-8); }
.c-section h2 { margin: 0 0 var(--space-3); font-size: 13px; font-weight: 600; color: var(--text-muted); }

.c-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.c-list { margin: 0; padding: 0; list-style: none; }
.c-list > li {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
}
.c-list > li:last-child { border-bottom: 0; }
.c-empty { padding: var(--space-6) var(--space-4); color: var(--text-muted); }

.c-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: var(--space-3); }
.c-tile { display: block; padding: var(--space-4); color: var(--text); }
.c-tile__name { display: block; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.c-badge {
  display: inline-block;
  padding: 0 var(--space-2);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
}
.c-tone-critical { color: var(--tone-critical-fg); background: var(--tone-critical-bg); }
.c-tone-warning { color: var(--tone-warning-fg); background: var(--tone-warning-bg); }
.c-tone-info { color: var(--tone-info-fg); background: var(--tone-info-bg); }
.c-tone-ok { color: var(--tone-ok-fg); background: var(--tone-ok-bg); }
.c-tone-muted { color: var(--tone-muted-fg); background: var(--tone-muted-bg); }

.c-spark { padding: var(--space-4); color: var(--accent); }
.c-spark svg { display: block; width: 100%; height: 48px; }

.c-banner {
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius);
}

.htmx-swapping { opacity: 0; transition: opacity 150ms ease-out; }
.htmx-settling { opacity: 1; transition: opacity 150ms ease-in; }

@media (prefers-reduced-motion: reduce) {
  .htmx-swapping, .htmx-settling { transition: none; }
}

@media (max-width: 720px) {
  body { grid-template-columns: 1fr; }
  .c-side { flex-direction: row; flex-wrap: wrap; border-right: 0; border-bottom: 1px solid var(--border); }
  .c-side__foot { margin-top: 0; margin-left: auto; flex-direction: row; }
  .c-main { padding: var(--space-4); }
}

@media print {
  body { display: block; }
  .c-side, .c-banner { display: none; }
}
```

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests/test_css.py -v`
Expected: 21 passed. If a pair fails, darken the light-theme foreground or lighten the
dark-theme foreground until it passes. Don't lower `MIN_CONTRAST`.

**Step 5: Commit**

```bash
git add static/console/console.css apps/console/_tests/test_css.py
git commit -m "feat(console): add the console stylesheet with contrast-checked tokens"
```

---

### Task 6: The Overview read model

**Files:**
- Create: `apps/console/queries/__init__.py` (empty)
- Create: `apps/console/queries/overview.py`
- Create: `apps/console/_tests/queries/__init__.py` (empty)
- Create: `apps/console/_tests/queries/test_overview.py`

Readiness comes from the existing `config.dashboard.build_readiness()`. Don't recompute
node freshness here. That function already excludes this instance's own `Node` row from
the peer totals, and a second copy of that rule would drift.

**Step 1: Write the failing tests**

`apps/console/_tests/queries/test_overview.py`:

```python
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import Alert, Incident, Node
from apps.console.queries import overview


def _incident(title, severity="warning", status="open", age_hours=0):
    incident = Incident.objects.create(title=title, severity=severity, status=status)
    Incident.objects.filter(pk=incident.pk).update(
        created_at=timezone.now() - timedelta(hours=age_hours)
    )
    return incident


def _alert(node, severity="warning", status="firing", incident=None, key="a"):
    return Alert.objects.create(
        fingerprint=f"{node.instance_id}:{key}",
        name=key,
        severity=severity,
        status=status,
        source="cluster",
        node=node,
        incident=incident,
        started_at=timezone.now(),
    )


@pytest.mark.parametrize(
    ("total", "critical", "expected"),
    [
        (0, 0, "No open incidents."),
        (1, 0, "1 open incident."),
        (3, 0, "3 open incidents."),
        (3, 2, "3 open incidents, 2 critical."),
    ],
)
def test_headline_reads_as_a_sentence(total, critical, expected):
    assert overview.headline(total, critical) == expected


@pytest.mark.django_db
def test_open_incidents_sort_by_severity_then_oldest_first(django_assert_num_queries):
    _incident("new warning", "warning", age_hours=1)
    _incident("old warning", "warning", age_hours=5)
    _incident("info", "info", age_hours=9)
    _incident("critical", "critical", status="acknowledged")
    _incident("resolved", "critical", status="resolved")
    _incident("closed", "critical", status="closed")

    with django_assert_num_queries(1):
        rows = overview.open_incidents()

    assert [row.title for row in rows] == ["critical", "old warning", "new warning", "info"]


@pytest.mark.django_db
def test_incident_row_carries_tones_count_and_a_link():
    incident = _incident("disk full", "critical")
    node = Node.objects.create(instance_id="n1", hostname="n1")
    _alert(node, incident=incident, key="a")
    _alert(node, incident=incident, key="b")

    (row,) = overview.open_incidents()

    assert row.severity_tone == "critical"
    assert row.status_tone == "critical"
    assert row.alert_count == 2
    assert row.url == reverse("admin:alerts_incident_change", args=[incident.pk])


@pytest.mark.django_db
def test_open_incidents_are_capped(monkeypatch):
    monkeypatch.setattr(overview, "INCIDENT_LIMIT", 2)
    for n in range(3):
        _incident(f"i{n}")

    assert len(overview.open_incidents()) == 2


@pytest.mark.django_db
def test_open_counts_ignore_the_cap(django_assert_num_queries):
    _incident("a", "critical")
    _incident("b", "warning")
    _incident("c", "critical", status="resolved")

    with django_assert_num_queries(1):
        counts = overview.open_counts()

    assert counts == (2, 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("alerts", "tone", "firing"),
    [
        ([], "ok", 0),
        ([("info", "firing")], "info", 1),
        ([("warning", "firing"), ("info", "firing")], "warning", 2),
        ([("critical", "firing"), ("warning", "firing")], "critical", 2),
        ([("critical", "resolved")], "ok", 0),
    ],
)
def test_node_tile_takes_the_worst_firing_tone(alerts, tone, firing):
    node = Node.objects.create(instance_id="n1", hostname="web-1")
    for index, (severity, status) in enumerate(alerts):
        _alert(node, severity=severity, status=status, key=str(index))

    (tile,) = overview.node_tiles()

    assert (tile.name, tile.tone, tile.firing) == ("web-1", tone, firing)
    assert tile.url == reverse("admin:alerts_node_change", args=[node.pk])


@pytest.mark.django_db
def test_node_tiles_cost_one_query_and_fall_back_to_the_instance_id(django_assert_num_queries):
    Node.objects.create(instance_id="b-id", hostname="")
    Node.objects.create(instance_id="a-id", hostname="alpha")

    with django_assert_num_queries(1):
        tiles = overview.node_tiles()

    assert [tile.name for tile in tiles] == ["alpha", "b-id"]


@pytest.mark.django_db
def test_spark_has_one_point_per_hour_and_counts_into_the_right_bucket(
    django_assert_num_queries,
):
    _incident("now")
    _incident("also now")
    _incident("three hours ago", age_hours=3)
    _incident("too old", age_hours=30)

    with django_assert_num_queries(1):
        points = overview.incident_spark()

    assert len(points) == overview.SPARK_HOURS
    assert [x for x, _ in points] == list(range(overview.SPARK_HOURS))
    assert points[-1][1] == 2
    assert points[-4][1] == 1
    assert sum(y for _, y in points) == 3


def test_attention_keeps_only_warn_and_error_and_maps_them_to_tones(monkeypatch):
    monkeypatch.setattr(
        overview,
        "build_readiness",
        lambda: [
            {"key": "a", "label": "Channels", "status": "ok", "detail": "1 active", "url": "/a"},
            {"key": "b", "label": "Inbox", "status": "warn", "detail": "3 stuck", "url": "/b"},
            {"key": "c", "label": "Provider", "status": "error", "detail": "none", "url": "/c"},
            {"key": "d", "label": "Nodes", "status": "info", "detail": "Standalone", "url": "/d"},
        ],
    )

    items = overview.attention()

    assert [(item.label, item.tone, item.detail, item.url) for item in items] == [
        ("Inbox", "warning", "3 stuck", "/b"),
        ("Provider", "critical", "none", "/c"),
    ]


@pytest.mark.django_db
def test_build_overview_assembles_every_part():
    _incident("disk full", "critical")
    Node.objects.create(instance_id="n1", hostname="n1")

    result = overview.build_overview()

    assert result.headline == "1 open incident, 1 critical."
    assert [row.title for row in result.incidents] == ["disk full"]
    assert [tile.name for tile in result.nodes] == ["n1"]
    assert len(result.spark) == overview.SPARK_HOURS
    assert isinstance(result.attention, tuple)
```

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests/queries/test_overview.py -v`
Expected: collection error, `ImportError: cannot import name 'overview'`.

**Step 3: Write the implementation**

`apps/console/queries/overview.py`:

```python
"""Read model for the Overview page. Returns dataclasses, never querysets."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.functions import TruncHour
from django.urls import reverse
from django.utils import timezone

from apps.alerts.models import AlertSeverity, AlertStatus, Incident, IncidentStatus, Node
from config.admin_badges import INCIDENT_STATUS_TONES, SEVERITY_TONES, tone_for
from config.dashboard import build_readiness

OPEN_STATUSES = [IncidentStatus.OPEN, IncidentStatus.ACKNOWLEDGED]
INCIDENT_LIMIT = 20
SPARK_HOURS = 24
ATTENTION_TONES = {"warn": "warning", "error": "critical"}

_SEVERITY_RANK = Case(
    When(severity=AlertSeverity.CRITICAL, then=Value(0)),
    When(severity=AlertSeverity.WARNING, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)


@dataclass(frozen=True)
class IncidentRow:
    title: str
    severity: str
    severity_tone: str
    status: str
    status_tone: str
    created_at: datetime
    alert_count: int
    url: str


@dataclass(frozen=True)
class NodeTile:
    name: str
    tone: str
    firing: int
    last_seen: datetime
    url: str


@dataclass(frozen=True)
class AttentionItem:
    label: str
    detail: str
    tone: str
    url: str


@dataclass(frozen=True)
class Overview:
    headline: str
    incidents: tuple[IncidentRow, ...]
    nodes: tuple[NodeTile, ...]
    spark: tuple[tuple[float, float], ...]
    attention: tuple[AttentionItem, ...]


def headline(total: int, critical: int) -> str:
    if not total:
        return "No open incidents."
    noun = "incident" if total == 1 else "incidents"
    if not critical:
        return f"{total} open {noun}."
    return f"{total} open {noun}, {critical} critical."


def open_counts() -> tuple[int, int]:
    counts = Incident.objects.filter(status__in=OPEN_STATUSES).aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
    )
    return counts["total"], counts["critical"]


def open_incidents() -> tuple[IncidentRow, ...]:
    incidents = (
        Incident.objects.filter(status__in=OPEN_STATUSES)
        .annotate(rank=_SEVERITY_RANK, alert_count=Count("alerts"))
        .order_by("rank", "created_at")[:INCIDENT_LIMIT]
    )
    return tuple(
        IncidentRow(
            title=incident.title,
            severity=incident.severity,
            severity_tone=tone_for(SEVERITY_TONES, incident.severity),
            status=incident.status,
            status_tone=tone_for(INCIDENT_STATUS_TONES, incident.status),
            created_at=incident.created_at,
            alert_count=incident.alert_count,
            url=reverse("admin:alerts_incident_change", args=[incident.pk]),
        )
        for incident in incidents
    )


def _node_tone(critical: int, warning: int, firing: int) -> str:
    if critical:
        return "critical"
    if warning:
        return "warning"
    if firing:
        return "info"
    return "ok"


def node_tiles() -> tuple[NodeTile, ...]:
    firing = Q(alerts__status=AlertStatus.FIRING)
    nodes = Node.objects.annotate(
        firing=Count("alerts", filter=firing),
        critical=Count("alerts", filter=firing & Q(alerts__severity=AlertSeverity.CRITICAL)),
        warning=Count("alerts", filter=firing & Q(alerts__severity=AlertSeverity.WARNING)),
    )
    tiles = [
        NodeTile(
            name=node.hostname or node.instance_id,
            tone=_node_tone(node.critical, node.warning, node.firing),
            firing=node.firing,
            last_seen=node.last_seen,
            url=reverse("admin:alerts_node_change", args=[node.pk]),
        )
        for node in nodes
    ]
    return tuple(sorted(tiles, key=lambda tile: tile.name))


def incident_spark() -> tuple[tuple[float, float], ...]:
    """Incidents opened per hour over the last ``SPARK_HOURS``, oldest first."""
    this_hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    first = this_hour - timedelta(hours=SPARK_HOURS - 1)
    # Buckets are cut in UTC so they match ``first`` whatever TIME_ZONE is set to.
    counts = dict(
        Incident.objects.filter(created_at__gte=first)
        .annotate(hour=TruncHour("created_at", tzinfo=dt_timezone.utc))
        .values_list("hour")
        .annotate(n=Count("id"))
        .order_by()
    )
    return tuple(
        (float(i), float(counts.get(first + timedelta(hours=i), 0))) for i in range(SPARK_HOURS)
    )


def attention() -> tuple[AttentionItem, ...]:
    return tuple(
        AttentionItem(
            label=entry["label"],
            detail=entry["detail"],
            tone=ATTENTION_TONES[entry["status"]],
            url=entry["url"],
        )
        for entry in build_readiness()
        if entry["status"] in ATTENTION_TONES
    )


def build_overview() -> Overview:
    return Overview(
        headline=headline(*open_counts()),
        incidents=open_incidents(),
        nodes=node_tiles(),
        spark=incident_spark(),
        attention=attention(),
    )
```

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests/queries/test_overview.py -v`
Expected: 17 passed.

If the spark test fails on bucket placement, print `counts.keys()` and `first`. The
likely cause is `timezone.now()` not being UTC-based, in which case build `first` from
`datetime.now(dt_timezone.utc)`.

**Step 5: Commit**

```bash
git add apps/console/queries apps/console/_tests/queries
git commit -m "feat(console): add the overview read model"
```

---

### Task 7: Overview page, base template and console.js

**Files:**
- Create: `templates/console/base.html`
- Modify: `templates/console/overview.html` (replace)
- Modify: `templates/console/partials/_overview.html` (replace)
- Create: `static/console/console.js`
- Modify: `apps/console/views/overview.py`
- Modify: `apps/console/_tests/views/test_overview.py`
- Create: `apps/console/_tests/test_templates.py`

**Step 1: Write the failing tests**

Add `import re` to the top of `apps/console/_tests/views/test_overview.py`, then append:

```python
@pytest.mark.django_db
def test_empty_install_reads_as_calm(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "No open incidents." in body
    assert "Nothing needs you right now." in body
    assert "No nodes have reported yet." in body


@pytest.mark.django_db
def test_page_shows_incidents_nodes_and_the_sparkline(client, staff):
    from apps.alerts.models import Incident, Node

    incident = Incident.objects.create(title="Disk full on web-1", severity="critical")
    node = Node.objects.create(instance_id="web-1", hostname="web-1")

    body = client.get(reverse("console:overview")).content.decode()

    assert "1 open incident, 1 critical." in body
    assert "Disk full on web-1" in body
    assert reverse("admin:alerts_incident_change", args=[incident.pk]) in body
    assert reverse("admin:alerts_node_change", args=[node.pk]) in body
    assert "c-tone-critical" in body
    assert "<svg" in body


@pytest.mark.django_db
def test_page_loads_only_first_party_assets_and_disables_htmx_eval(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert "console/vendor/htmx.min.js" in body
    assert "console/console.js" in body
    assert "console/console.css" in body
    assert '"allowEval":false' in body
    assert not re.search(r'(?:src|href)="(?:https?:)?//', body)


@pytest.mark.django_db
def test_page_polls_itself_and_links_onward(client, staff):
    body = client.get(reverse("console:overview")).content.decode()

    assert f'hx-get="{reverse("console:overview")}"' in body
    assert 'hx-trigger="every 30s"' in body
    assert reverse("admin:index") in body
    assert reverse("admin:logout") in body


@pytest.mark.django_db
def test_readiness_problems_are_listed(client, staff, monkeypatch):
    from apps.console.queries import overview

    monkeypatch.setattr(
        overview,
        "build_readiness",
        lambda: [
            {"key": "x", "label": "Inbox", "status": "warn", "detail": "3 stuck", "url": "/x/"}
        ],
    )

    body = client.get(reverse("console:overview")).content.decode()

    assert "Inbox" in body and "3 stuck" in body
    assert "System ready." not in body
```

`apps/console/_tests/test_templates.py`:

```python
"""Console templates carry no inline script, style or handlers.

The CSP would block them in a browser. This fails in CI first.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

TEMPLATES = sorted((Path(settings.BASE_DIR) / "templates" / "console").rglob("*.html"))

FORBIDDEN = {
    "style element": re.compile(r"<style\b", re.I),
    "style attribute": re.compile(r"\sstyle\s*=", re.I),
    "inline script": re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I),
    "event handler attribute": re.compile(r"\son[a-z]+\s*=", re.I),
    "hx-on attribute": re.compile(r"\shx-on", re.I),
    "javascript url": re.compile(r"javascript:", re.I),
    "external url": re.compile(r"(?:src|href)\s*=\s*[\"'](?:https?:)?//", re.I),
}


def test_there_are_templates_to_scan():
    assert TEMPLATES


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: path.name)
@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_template_is_clean(template, label):
    assert not FORBIDDEN[label].search(template.read_text()), f"{label} in {template}"
```

**Step 2: Run the tests and watch them fail**

Run: `uv run pytest apps/console/_tests -v`
Expected: the five new view tests fail on missing text. The template guard passes
already, since the placeholders are clean.

**Step 3: Write the implementation**

`apps/console/views/overview.py`:

```python
from apps.checkers.admin_charts import render_sparkline
from apps.console.queries.overview import build_overview
from apps.console.views.base import ConsoleView


class OverviewView(ConsoleView):
    template_name = "console/overview.html"
    partial_template_name = "console/partials/_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        overview = build_overview()
        context["overview"] = overview
        context["spark"] = render_sparkline(overview.spark, width=600, height=48)
        context["nav"] = "overview"
        return context
```

`templates/console/base.html`:

```html
{% load static %}<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="htmx-config" content='{"allowEval":false,"allowScriptTags":false,"includeIndicatorStyles":false,"selfRequestsOnly":true}'>
  <title>{% block title %}Console{% endblock %}</title>
  <link rel="stylesheet" href="{% static 'console/console.css' %}">
  <script src="{% static 'console/vendor/htmx.min.js' %}" defer></script>
  <script src="{% static 'console/console.js' %}" defer></script>
</head>
<body>
  <nav class="c-side" aria-label="Console">
    <p class="c-side__brand">Ops console</p>
    <a href="{% url 'console:overview' %}"{% if nav == "overview" %} aria-current="page"{% endif %}>Overview</a>
    <div class="c-side__foot">
      <a href="{% url 'admin:index' %}">Admin</a>
      <form method="post" action="{% url 'admin:logout' %}">
        {% csrf_token %}
        <button type="submit">Sign out</button>
      </form>
    </div>
  </nav>
  <main class="c-main">
    <p id="c-banner" class="c-banner c-tone-warning" role="status" hidden>
      Couldn't refresh. Showing the last data that loaded.
    </p>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

The sidebar lists only pages that exist. Each later PR adds its own entry.

`templates/console/overview.html`:

```html
{% extends "console/base.html" %}
{% block title %}Overview · Console{% endblock %}
{% block content %}
<div id="overview" data-poll hx-get="{% url 'console:overview' %}" hx-trigger="every 30s" hx-swap="innerHTML">
  {% include "console/partials/_overview.html" %}
</div>
{% endblock %}
```

`templates/console/partials/_overview.html`:

```html
<h1 class="c-headline">{{ overview.headline }}</h1>
{% if overview.attention %}
<ul class="c-list c-card">
  {% for item in overview.attention %}
  <li>
    <span class="c-badge c-tone-{{ item.tone }}">{{ item.tone }}</span>
    <span><a href="{{ item.url }}">{{ item.label }}</a> <span class="c-muted">{{ item.detail }}</span></span>
  </li>
  {% endfor %}
</ul>
{% else %}
<p class="c-muted">System ready.</p>
{% endif %}

<section class="c-section">
  <h2>Open incidents</h2>
  <div class="c-card">
    {% if overview.incidents %}
    <ul class="c-list">
      {% for row in overview.incidents %}
      <li>
        <span class="c-badge c-tone-{{ row.severity_tone }}">{{ row.severity }}</span>
        <a href="{{ row.url }}">{{ row.title }}</a>
        <span class="c-muted">{{ row.alert_count }} alert{{ row.alert_count|pluralize }}</span>
        <span>
          <span class="c-badge c-tone-{{ row.status_tone }}">{{ row.status }}</span>
          <time class="c-muted" datetime="{{ row.created_at|date:'c' }}" title="{{ row.created_at|date:'Y-m-d H:i:s T' }}">{{ row.created_at|timesince }} ago</time>
        </span>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p class="c-empty">Nothing needs you right now.</p>
    {% endif %}
  </div>
</section>

<section class="c-section">
  <h2>Nodes</h2>
  {% if overview.nodes %}
  <div class="c-grid">
    {% for tile in overview.nodes %}
    <a class="c-card c-tile" href="{{ tile.url }}">
      <span class="c-tile__name">{{ tile.name }}</span>
      <span class="c-badge c-tone-{{ tile.tone }}">{% if tile.firing %}{{ tile.firing }} firing{% else %}healthy{% endif %}</span>
      <time class="c-muted" datetime="{{ tile.last_seen|date:'c' }}" title="{{ tile.last_seen|date:'Y-m-d H:i:s T' }}">seen {{ tile.last_seen|timesince }} ago</time>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <p class="c-card c-empty">No nodes have reported yet.</p>
  {% endif %}
</section>

<section class="c-section">
  <h2>Incidents opened, last 24 hours</h2>
  <div class="c-card c-spark">{{ spark }}</div>
</section>
```

`static/console/console.js`:

```js
(function () {
  "use strict";

  var banner = document.getElementById("c-banner");

  // htmx's own trigger filters need eval, which the CSP forbids, so polling is paused here.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var element = event.detail.elt;
    if (!element.hasAttribute("data-poll")) {
      return;
    }
    if (document.hidden || element.querySelector("details[open]")) {
      event.preventDefault();
    }
  });

  function showBanner() {
    banner.hidden = false;
  }

  document.addEventListener("htmx:responseError", showBanner);
  document.addEventListener("htmx:sendError", showBanner);
  document.addEventListener("htmx:afterOnLoad", function () {
    banner.hidden = true;
  });
})();
```

**Step 4: Run the tests and watch them pass**

Run: `uv run pytest apps/console/_tests -v`
Expected: all pass.

`render_sparkline` writes `font-size` and `fill` as SVG presentation attributes. Those
aren't `style=` attributes, so the CSP allows them and the template guard doesn't see
them.

**Step 5: Check it in a browser**

```bash
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/` signed in as a staff user. Confirm each of these and
report any that fail, with what you saw:

- `/` lands on `/console/` and the page is styled.
- The browser console shows zero CSP violations.
- The network tab shows a request to `/console/` every 30 seconds with an
  `HX-Request: true` header, and it stops while the tab is in the background.
- Stopping `runserver` makes the banner appear on the next poll. Restarting clears it.
- Dark mode follows the OS setting.
- "Sign out" works, and the next poll sends the browser to the login page.

No automated test covers `console.js`. This manual pass is its only verification, so
say plainly in the PR description which of the six checks were done.

**Step 6: Commit**

```bash
git add apps/console templates/console static/console/console.js
git commit -m "feat(console): render the overview with live refresh"
```

---

### Task 8: Documentation

**Files:**
- Create: `apps/console/AGENTS.md`
- Modify: `AGENTS.md` (root)

**Step 1: Write `apps/console/AGENTS.md`**

```markdown
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
- **Every view subclasses `ConsoleView`** (`views/base.py`). It enforces staff access,
  sets the CSP and serves `partial_template_name` to htmx requests.
- **No inline script, style or handlers** in `templates/console/`. The CSP blocks them
  and `_tests/test_templates.py` fails on them. htmx runs with eval disabled, so
  `hx-on` and bracketed trigger filters don't work. Put behaviour in
  `static/console/console.js`.
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
`.github/workflows/vendor-advisories.yml` checks the pinned version against OSV weekly.

To upgrade htmx, follow the steps in `static/console/vendor/VENDOR.md`. Don't add a
second vendored library without a design that names why first-party code won't do.
```

**Step 2: Update the root `AGENTS.md`**

Add `apps/console/AGENTS.md` to the app-local rules list under "Documentation map".

Add this row to the "Core apps" table:

```markdown
| `console` | Operator console at `/console/`. Server-rendered, staff only. A consumer: not a stage, not a producer | none |
```

**Step 3: Commit**

```bash
git add apps/console/AGENTS.md AGENTS.md
git commit -m "docs(console): document the console app and its frontend rules"
```

---

### Task 9: Verify and open the PR

Use superpowers:verification-before-completion. Run each command and read its output
before claiming anything.

```bash
uv run black . --check
uv run ruff check .
uv run pytest
uv run coverage run -m pytest && uv run coverage report --include="apps/console/*"
uv run bandit -r apps/ config/ -c pyproject.toml
uv run pip-audit --strict --desc
uv run python manage.py check
```

Expected: all clean, and every `apps/console` file at 100% branch coverage. If a branch
is uncovered, add a test. Don't add `pragma: no cover`.

Then push the branch and open a PR against `main`. The description states what shipped,
which of the six browser checks from Task 7 were performed, and whether the advisory
workflow's failure path was proven in Task 4.

---

## Later PRs, in outline

Each gets its own detailed plan before work starts.

**PR 2: Incidents, read-only.** `filters.py` with a typed `IncidentFilter`.
`queries/incidents.py` for the list. Incident detail renders
`apps.alerts.timeline.build_incident_timeline` and `apps.alerts.diagnosis.diagnose_incident`.
Extend the timeline with alert and notification entries only where it lacks them.
Per-stage output renderers with raw JSON behind `<details>`. Overview rows switch from
admin links to console links. Print stylesheet.

**PR 3: Operator actions.** POST-only views for acknowledge, resolve and close through
`IncidentManager` in `apps/alerts/services.py`. Lift run retry and mark-failed out of
`PipelineRunAdmin`, and the re-evaluate flow out of `AlertAdmin` and `NodeAdmin`, into
their owning apps. Both surfaces call the lifted functions. Check the two open
follow-ups from #238 before starting: the confirm-form double submit and
`resolve_selected` writing no history.

**PR 4: Alerts and Runs.** List and detail for each. The run page reuses
`diagnose_incident` output and the stage renderers from PR 2.

**PR 5: Nodes and System.** The node page reuses `apps/alerts/node_overview.py`. System
reuses `config/netmap.py` and `apps/alerts/policy_overview.py`. Those modules build
admin URLs today, so they need a way to emit console URLs without forking.

**PR 6: Reports, exports, retirement.** Aggregate queries with a 90-day cap. Stacked
bar and severity strip renderers beside `render_sparkline`. Streaming CSV and JSON
exports capped at 50,000 rows. Then remove the admin dashboard, `ops.css`, `SECTION_MAP`
and their tests, leaving `/admin/` as stock Django.

{% endraw %}
