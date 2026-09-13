---
title: "Admin Ops Skin Implementation Plan"
parent: Plans
---

{% raw %}

# Admin Ops Skin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the Django admin a modern dark-first ops-console skin without changing any URL, model registration, field set or action.

**Architecture:** A stylesheet layer that redefines Django 5.2's admin CSS custom properties, loaded via an overridden `templates/admin/base_site.html`. All status color moves out of ~90 hardcoded hexes in Python and templates into one `badge()` / `tinted()` helper backed by five semantic tones. No new dependency, no change to the admin site class or any `ModelAdmin`.

**Tech Stack:** Django 5.2 admin, plain CSS custom properties, pytest + pytest-django.

**Design doc:** `docs/plans/2026-09-12-admin-skin-design.md`

---

## Ground rules for the implementer

- Run everything through `uv run`. Never call `python` or `pytest` bare.
- Line length is 100 (Black + Ruff, configured in `pyproject.toml`).
- Absolute imports only: `from config.admin_badges import badge`.
- Pre-commit runs the **full pytest suite** on every commit. It takes a few minutes. That is expected, not a hang.
- 100% branch coverage on changed Python. Verify with
  `uv run coverage run -m pytest && uv run coverage report`.
- Do not touch `apps/notify/drivers/base.py`. It has its own `SEVERITY_COLORS`, but those
  are Slack attachment colors on outbound notifications, not admin UI. Out of scope.
- Do not touch the login page or `admindocs`.

## The tone vocabulary

Five tones replace every status hex in the admin. Memorise this mapping, it is used in
almost every task:

| Old hex | Tone |
|---|---|
| `#dc3545`, `#b00020`, `#b00` | `critical` |
| `#ffc107`, `#b26a00`, `#f0ad4e` | `warning` |
| `#17a2b8` | `info` |
| `#28a745`, `#2e7d32` | `ok` |
| `#6c757d`, `#888`, `#999`, `#ccc`, `#666` | `muted` |

---

### Task 1: Static plumbing and the token layer

**Files:**
- Create: `static/admin/css/ops.css`
- Create: `templates/admin/base_site.html`
- Modify: `config/settings.py` (near line 151, the `STATIC_URL` block)
- Test: `config/_tests/test_settings.py`, `config/_tests/test_dashboard_render.py`

**Step 1: Write the failing tests**

Add to `config/_tests/test_settings.py`:

```python
def test_staticfiles_dirs_includes_project_static():
    from django.conf import settings

    assert settings.BASE_DIR / "static" in settings.STATICFILES_DIRS
```

Add to `config/_tests/test_dashboard_render.py` (match the existing client/login fixture
style already in that file):

```python
def test_dashboard_links_the_ops_stylesheet(admin_client):
    body = admin_client.get("/admin/").content.decode()
    assert "admin/css/ops.css" in body
```

**Step 2: Run them to verify they fail**

```bash
uv run pytest config/_tests/test_settings.py::test_staticfiles_dirs_includes_project_static \
             config/_tests/test_dashboard_render.py::test_dashboard_links_the_ops_stylesheet -v
```

Expected: both FAIL. The first with `AttributeError: 'Settings' object has no attribute
'STATICFILES_DIRS'`, the second with an assertion error.

**Step 3: Add the settings line**

In `config/settings.py`, directly after `STATIC_ROOT = BASE_DIR / "staticfiles"`:

```python
STATICFILES_DIRS = [BASE_DIR / "static"]
```

**Step 4: Create `templates/admin/base_site.html`**

```html
{% extends "admin/base.html" %}
{% load static %}

{% comment %}
Single hook for the ops skin. Every admin page reaches this template, including the
django_object_actions change_form and change_list, which extend admin/change_form.html
and admin/change_list.html rather than this file directly.

ops.css is loaded after block.super so it wins over Django's base.css without any
!important. It redefines Django's own custom properties rather than re-selecting.
{% endcomment %}

{% block title %}{% if subtitle %}{{ subtitle }} | {% endif %}{{ title }} | {{ site_title|default:_('Django site admin') }}{% endblock %}

{% block extrastyle %}{{ block.super }}
<link rel="stylesheet" href="{% static 'admin/css/ops.css' %}">
{% endblock %}

{% block branding %}
<div id="site-name"><a href="{% url 'admin:index' %}">{{ site_header|default:_('Django administration') }}</a></div>
{% if user.is_anonymous %}{% include "admin/color_theme_toggle.html" %}{% endif %}
{% endblock %}

{% block nav-global %}{% endblock %}
```

> **Superseded 2026-09-13.** The implementation loads `ops.css` from `extrahead`, not `extrastyle`. `admin/change_list.html` and `admin/change_form.html` append their own styles after `block.super`, so an `extrastyle` link loses ties on load order. The original text above is left as written; `templates/admin/base_site.html` is the truth.

**Step 5: Create `static/admin/css/ops.css` with the token layer only**

Styling of individual surfaces comes in later tasks. This step establishes only the
variables. Django 5.2 defines these under `:root` in `admin/css/base.css` and swaps them
under `[data-theme="dark"]` plus a `prefers-color-scheme` media query, so redefining them
here reaches every admin page in both themes.

```css
/*
 * Ops console skin. Redefines Django 5.2's admin custom properties rather than
 * re-selecting its rules, so the built-in light/dark/auto toggle keeps working.
 */

:root {
  --ops-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --ops-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;

  --ops-radius: 6px;
  --ops-radius-sm: 4px;
  --ops-space-1: 4px;
  --ops-space-2: 8px;
  --ops-space-3: 12px;
  --ops-space-4: 16px;
  --ops-space-6: 24px;

  --ops-critical-fg: #f87171;
  --ops-critical-bg: #7f1d1d;
  --ops-warning-fg: #fbbf24;
  --ops-warning-bg: #78350f;
  --ops-info-fg: #60a5fa;
  --ops-info-bg: #1e3a5f;
  --ops-ok-fg: #4ade80;
  --ops-ok-bg: #14532d;
  --ops-muted-fg: #9ca3af;
  --ops-muted-bg: #374151;
}

/* Light theme: the same five tones, re-tuned for a light ground. */
:root,
[data-theme="light"] {
  --ops-critical-fg: #b91c1c;
  --ops-critical-bg: #fee2e2;
  --ops-warning-fg: #92400e;
  --ops-warning-bg: #fef3c7;
  --ops-info-fg: #1e40af;
  --ops-info-bg: #dbeafe;
  --ops-ok-fg: #166534;
  --ops-ok-bg: #dcfce7;
  --ops-muted-fg: #4b5563;
  --ops-muted-bg: #e5e7eb;

  --primary: #1f2937;
  --secondary: #111827;
  --accent: #2563eb;
  --link-fg: #2563eb;
  --link-hover-color: #1d4ed8;
  --header-bg: #111827;
  --header-color: #f9fafb;
  --header-branding-color: #f9fafb;
  --header-link-color: #e5e7eb;
  --breadcrumbs-bg: #1f2937;
  --breadcrumbs-fg: #d1d5db;
  --breadcrumbs-link-fg: #f9fafb;
}

[data-theme="dark"] {
  --ops-critical-fg: #f87171;
  --ops-critical-bg: #7f1d1d;
  --ops-warning-fg: #fbbf24;
  --ops-warning-bg: #78350f;
  --ops-info-fg: #60a5fa;
  --ops-info-bg: #1e3a5f;
  --ops-ok-fg: #4ade80;
  --ops-ok-bg: #14532d;
  --ops-muted-fg: #9ca3af;
  --ops-muted-bg: #374151;

  --body-bg: #0b0f14;
  --body-fg: #e5e7eb;
  --body-quiet-color: #9ca3af;
  --body-loud-color: #f9fafb;
  --hairline-color: #1f2937;
  --border-color: #1f2937;
  --darkened-bg: #111721;
  --selected-bg: #111721;
  --selected-row: #1c2330;
  --module-bg: #111721;
  --primary: #0b0f14;
  --accent: #60a5fa;
  --link-fg: #60a5fa;
  --link-hover-color: #93c5fd;
  --header-bg: #0b0f14;
  --header-color: #e5e7eb;
  --breadcrumbs-bg: #111721;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    /* Duplicate of the [data-theme="dark"] block above. Django's own base.css
       does the same, because the auto state stamps no attribute on the root. */
    --body-bg: #0b0f14;
    --body-fg: #e5e7eb;
    --body-quiet-color: #9ca3af;
    --hairline-color: #1f2937;
    --border-color: #1f2937;
    --darkened-bg: #111721;
    --module-bg: #111721;
    --accent: #60a5fa;
    --link-fg: #60a5fa;
    --header-bg: #0b0f14;

    --ops-critical-fg: #f87171;
    --ops-critical-bg: #7f1d1d;
    --ops-warning-fg: #fbbf24;
    --ops-warning-bg: #78350f;
    --ops-info-fg: #60a5fa;
    --ops-info-bg: #1e3a5f;
    --ops-ok-fg: #4ade80;
    --ops-ok-bg: #14532d;
    --ops-muted-fg: #9ca3af;
    --ops-muted-bg: #374151;
  }
}

body {
  font-family: var(--ops-sans);
}

/* The badge and tint system emitted by config/admin_badges.py. */
.ops-badge {
  display: inline-block;
  padding: 2px var(--ops-space-2);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.6;
  letter-spacing: 0.02em;
  text-decoration: none;
  white-space: nowrap;
}

a.ops-badge:hover {
  filter: brightness(1.15);
}

.ops-badge--critical { color: var(--ops-critical-fg); background: var(--ops-critical-bg); }
.ops-badge--warning  { color: var(--ops-warning-fg);  background: var(--ops-warning-bg); }
.ops-badge--info     { color: var(--ops-info-fg);     background: var(--ops-info-bg); }
.ops-badge--ok       { color: var(--ops-ok-fg);       background: var(--ops-ok-bg); }
.ops-badge--muted    { color: var(--ops-muted-fg);    background: var(--ops-muted-bg); }

.ops-tint--critical { color: var(--ops-critical-fg); }
.ops-tint--warning  { color: var(--ops-warning-fg); }
.ops-tint--info     { color: var(--ops-info-fg); }
.ops-tint--ok       { color: var(--ops-ok-fg); }
.ops-tint--muted    { color: var(--ops-muted-fg); }

/* Identifiers are read character by character. */
.ops-mono,
.field-trace_id, .field-run_id, .field-instance_id, .field-fingerprint,
td.field-trace_id, td.field-run_id, td.field-instance_id, td.field-fingerprint {
  font-family: var(--ops-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
```

**Step 6: Run the tests**

```bash
uv run pytest config/_tests/test_settings.py config/_tests/test_dashboard_render.py -v
```

Expected: PASS.

**Step 7: Eyeball it**

```bash
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/admin/`. The header should be near-black in light mode. Toggle
the theme in the header. Dark mode should be the new near-black, not Django's default grey.
Nothing should be unreadable. Stop the server.

**Step 8: Commit**

```bash
git add static/admin/css/ops.css templates/admin/base_site.html config/settings.py \
        config/_tests/test_settings.py config/_tests/test_dashboard_render.py
git commit -m "feat(admin): add the ops skin token layer"
```

---

### Task 2: The badge helper

**Files:**
- Create: `config/admin_badges.py`
- Test: `config/_tests/test_admin_badges.py`

**Step 1: Write the failing tests**

```python
"""The single source of admin status colour."""

import pytest
from django.utils.safestring import SafeString

from config.admin_badges import TONES, badge, tinted


def test_badge_renders_its_tone_class():
    assert badge("OPEN", "critical") == (
        '<span class="ops-badge ops-badge--critical">OPEN</span>'
    )


def test_badge_returns_safe_string():
    assert isinstance(badge("OPEN", "ok"), SafeString)


def test_badge_escapes_its_text():
    # Alert names and hostnames arrive over a webhook.
    assert "&lt;script&gt;" in badge("<script>", "muted")


def test_badge_with_url_renders_an_anchor():
    assert badge("3 CRITICAL", "critical", url="/admin/x/?a=1") == (
        '<a class="ops-badge ops-badge--critical" href="/admin/x/?a=1">3 CRITICAL</a>'
    )


def test_badge_escapes_its_url():
    assert '"' not in badge("x", "ok", url='/a/?b="c"').split("href=")[1].split(">")[0][1:-1]


def test_badge_rejects_an_unknown_tone():
    # Silently emitting an unclassed span is how a status goes invisible.
    with pytest.raises(ValueError, match="unknown badge tone"):
        badge("OPEN", "danger")


def test_tinted_renders_its_tone_class():
    assert tinted("stalled", "warning") == '<span class="ops-tint ops-tint--warning">stalled</span>'


def test_tinted_rejects_an_unknown_tone():
    with pytest.raises(ValueError, match="unknown badge tone"):
        tinted("x", "purple")


def test_tones_are_the_five_documented_ones():
    assert TONES == frozenset({"critical", "warning", "info", "ok", "muted"})
```

**Step 2: Run to verify they fail**

```bash
uv run pytest config/_tests/test_admin_badges.py -v
```

Expected: all FAIL with `ModuleNotFoundError: No module named 'config.admin_badges'`.

**Step 3: Write the implementation**

```python
"""Status colour for the admin, in one place.

Every status pill and coloured status word in the admin goes through here. Colour
itself lives in ``static/admin/css/ops.css`` under five tone classes, so a theme
change is a stylesheet edit rather than a sweep through five ``admin.py`` files.
"""

from django.utils.html import format_html
from django.utils.safestring import SafeString

TONES = frozenset({"critical", "warning", "info", "ok", "muted"})


def _check(tone: str) -> None:
    if tone not in TONES:
        raise ValueError(f"unknown badge tone: {tone!r}")


def badge(text, tone: str, *, url: str | None = None) -> SafeString:
    """A status pill. Renders as an anchor when ``url`` is given."""
    _check(tone)
    if url is None:
        return format_html('<span class="ops-badge ops-badge--{}">{}</span>', tone, text)
    return format_html(
        '<a class="ops-badge ops-badge--{}" href="{}">{}</a>', tone, url, text
    )


def tinted(text, tone: str) -> SafeString:
    """Coloured inline text with no pill. For glyph strips and inline warnings."""
    _check(tone)
    return format_html('<span class="ops-tint ops-tint--{}">{}</span>', tone, text)
```

**Step 4: Run the tests**

```bash
uv run pytest config/_tests/test_admin_badges.py -v
```

Expected: PASS, 9 tests.

**Step 5: Commit**

```bash
git add config/admin_badges.py config/_tests/test_admin_badges.py
git commit -m "feat(admin): add the badge helper, one source of status colour"
```

---

### Task 3: Migrate `apps/checkers/admin.py`

**Files:**
- Modify: `apps/checkers/admin.py:80-95`
- Test: `apps/checkers/_tests/` (find the existing admin test module, or create
  `apps/checkers/_tests/test_admin.py` following the shape of `apps/notify/_tests/test_admin.py`)

**Step 1: Write the failing test**

```python
def test_status_badge_uses_the_ok_tone(db):
    run = CheckRun.objects.create(checker_name="cpu", status="ok")
    html = CheckRunAdmin(CheckRun, site).status_badge(run)
    assert "ops-badge--ok" in html
    assert "#" not in html


def test_status_badge_falls_back_to_muted(db):
    run = CheckRun.objects.create(checker_name="cpu", status="weird")
    assert "ops-badge--muted" in CheckRunAdmin(CheckRun, site).status_badge(run)
```

Adjust the `CheckRun` construction to whatever its required fields actually are. Read
`apps/checkers/models.py` first.

**Step 2: Run to verify it fails**

```bash
uv run pytest apps/checkers/_tests/test_admin.py -v
```

Expected: FAIL, the rendered HTML still contains `#28a745`.

**Step 3: Replace the method**

```python
    _STATUS_TONES = {
        "ok": "ok",
        "warning": "warning",
        "critical": "critical",
        "unknown": "muted",
    }

    @admin.display(description="Status")
    def status_badge(self, obj):
        return badge(obj.status.upper(), self._STATUS_TONES.get(obj.status, "muted"))
```

Add `from config.admin_badges import badge` to the imports. Remove the now-unused
`format_html` import only if nothing else in the file uses it.

**Step 4: Run the tests**

```bash
uv run pytest apps/checkers/ -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/checkers/
git commit -m "refactor(admin): move the checkers status badge onto tones"
```

---

### Task 4: Migrate `apps/orchestration/admin.py`

**Files:**
- Modify: `apps/orchestration/admin.py:210-250` (the stage strip), `:423` (the inactive marker)
- Test: `apps/orchestration/_tests/test_admin.py`

**Step 1: Write the failing test**

```python
def test_stage_strip_uses_tones_not_hexes(db):
    # build a PipelineRun with one SUCCEEDED and one FAILED StageExecution,
    # following the fixtures already in this module
    html = PipelineRunAdmin(PipelineRun, site).stage_progress(run)
    assert "ops-tint--ok" in html
    assert "ops-tint--critical" in html
    assert "#" not in html
```

**Step 2: Run to verify it fails.** Expected: `#28a745` still present.

**Step 3: Replace the colour branch**

```python
            if status == StageStatus.SUCCEEDED:
                tone, icon = "ok", "✓"
            elif status == StageStatus.RUNNING:
                tone, icon = "warning", "●"
            elif status == StageStatus.FAILED:
                tone, icon = "critical", "✗"
            else:
                tone, icon = "muted", "○"
            part = format_html(
                '<span class="ops-stage">{}<br>'
                '<span class="ops-stage-label">{}</span></span>',
                tinted(icon, tone),
                stage_label,
            )
```

And the separator:

```python
        separator = tinted("→", "muted")
```

And line 423:

```python
        return format_html('{} {}', obj.name, tinted("(inactive)", "muted"))
```

Add to `ops.css`:

```css
.ops-stage { display: inline-block; text-align: center; margin: 0 var(--ops-space-1); }
.ops-stage .ops-tint { font-size: 18px; }
.ops-stage-label { font-size: 11px; }
```

**Step 4: Run** `uv run pytest apps/orchestration/ -v`. Expected: PASS.

**Step 5: Commit**

```bash
git add apps/orchestration/ static/admin/css/ops.css
git commit -m "refactor(admin): move the stage strip onto tones"
```

---

### Task 5: Migrate the Alert badges in `apps/alerts/admin.py`

**Files:**
- Modify: `apps/alerts/admin.py:245-268` (severity_badge, status_badge), `:305` (no-incident marker)
- Test: `apps/alerts/_tests/test_alert_admin_reeval.py` or a new `test_alert_admin_badges.py`

**Step 1: Write the failing tests** asserting `ops-badge--critical` for a critical alert,
`ops-badge--ok` for a resolved one, `ops-badge--muted` for an unknown status, and that no
`#` appears in any of the three.

**Step 2: Run to verify they fail.**

**Step 3: Replace**

```python
    @admin.display(description="Severity")
    def severity_badge(self, obj):
        return badge(obj.severity.upper(), SEVERITY_TONES.get(obj.severity, "muted"))

    _STATUS_TONES = {"firing": "critical", "resolved": "ok"}

    @admin.display(description="Status")
    def status_badge(self, obj):
        return badge(obj.status.upper(), self._STATUS_TONES.get(obj.status, "muted"))
```

Line 305 becomes:

```python
                format_html(
                    "<div>{} (no incident; ingest not run)</div>",
                    tinted(format_html("<b>{}</b>", obj.name), "critical"),
                ),
```

`SEVERITY_TONES` is new, added in Task 8 alongside the `SEVERITY_COLORS` removal. For this
task, define it in `apps/alerts/node_overview.py` next to `SEVERITY_COLORS` and import it
here. `SEVERITY_COLORS` gets deleted in Task 8, not now.

```python
SEVERITY_TONES: dict[str, str] = {
    AlertSeverity.CRITICAL: "critical",
    AlertSeverity.WARNING: "warning",
    AlertSeverity.INFO: "info",
}
```

**Step 4: Run** `uv run pytest apps/alerts/ -v`.

**Step 5: Commit**

```bash
git add apps/alerts/
git commit -m "refactor(admin): move the Alert badges onto tones"
```

---

### Task 6: Migrate the Incident badges in `apps/alerts/admin.py`

**Files:**
- Modify: `apps/alerts/admin.py:544-590` (severity_badge, status_badge, firing_alert_count_display)
- Test: same module as Task 5

Same shape as Task 5. The status map is:

```python
    _STATUS_TONES = {
        "open": "critical",
        "acknowledged": "warning",
        "resolved": "ok",
        "closed": "muted",
    }
```

`firing_alert_count_display` line 585 becomes:

```python
            tinted(format_html("<b>{}</b>", count), "critical"),
```

Steps 1-5 as before. Commit message:
`refactor(admin): move the Incident badges onto tones`.

---

### Task 7: Migrate the stage-diagnosis strip

**Files:**
- Modify: `apps/alerts/admin.py:605-660` (`_STATUS_RENDER`, `diagnosis_display`, `_render_status`),
  `:678`, `:688`, `:726`
- Test: `apps/alerts/_tests/` — find the module that already covers `diagnosis_display`

**Step 1: Write the failing test** asserting the strip renders `ops-tint--ok` for an `ok`
stage and `ops-tint--critical` for a `failed` one, and that no `#` remains.

**Step 3: Replace**

```python
    # status -> (glyph, tone, label). "stalled" reads "running / stalled" so a
    # legitimately in-flight stage is not misread as stuck.
    _STATUS_RENDER = {
        "ok": ("✓", "ok", "ok"),
        "empty": ("✓→∅", "warning", "empty"),
        "failed": ("✗", "critical", "failed"),
        "stalled": ("…", "warning", "running / stalled"),
        "skipped": ("⊘", "muted", "skipped"),
        "never_ran": ("✗", "critical", "never ran"),
    }
```

```python
    def _render_status(self, entry):
        glyph, tone, label = self._STATUS_RENDER.get(
            entry["status"], ("?", "muted", entry["status"])
        )
        body = tinted(format_html("{} {}", glyph, label), tone)
```

The three remaining `#888` / `#b00` sites at 633, 678, 688 and 726 become `tinted(..., "muted")`
and `tinted(..., "critical")` respectively.

**Steps 2, 4, 5** as before. Commit message:
`refactor(admin): move the stage diagnosis strip onto tones`.

---

### Task 8: Migrate `apps/alerts/node_overview.py`

**Files:**
- Modify: `apps/alerts/node_overview.py:28-32` (delete `SEVERITY_COLORS`), `:110-125` (chips),
  `:338` and `:357` (`IncidentRow.color` → `.tone`)
- Modify: `apps/alerts/admin.py` (drop the `SEVERITY_COLORS` import)
- Modify: `templates/admin/alerts/node/change_form.html` (the incident rows read `row.color`)
- Test: `apps/alerts/_tests/test_node_overview.py:464` and `:470`

**Step 1: Rewrite the two coupled assertions**

`test_node_overview.py:464` and `:470` currently assert on hex values. Replace with:

```python
        self.assertEqual(row.tone, "critical")
```

and

```python
        self.assertEqual(build_incident_rows(node)[0].tone, "muted")
```

Update the module's import at line 14 from `SEVERITY_COLORS` to `SEVERITY_TONES`.

**Step 2: Run to verify they fail.**

```bash
uv run pytest apps/alerts/_tests/test_node_overview.py -v
```

Expected: FAIL with `AttributeError: 'IncidentRow' object has no attribute 'tone'`.

**Step 3: Implement**

Delete `SEVERITY_COLORS`. `SEVERITY_TONES` (added in Task 5) stays.

Chips, at line 117:

```python
        parts.append(badge(f"{count} {severity.upper()}", SEVERITY_TONES.get(severity, "muted"), url=url))
```

`IncidentRow`: rename the field `color: str` to `tone: str`, and line 357:

```python
            tone=SEVERITY_TONES.get(incident.severity, "muted"),
```

In `templates/admin/alerts/node/change_form.html`, every `{{ row.color }}` in a `style`
attribute becomes `class="ops-badge ops-badge--{{ row.tone }}"`.

**Step 4: Run** `uv run pytest apps/alerts/ -v`. Expected: PASS.

**Step 5: Commit**

```bash
git add apps/alerts/ templates/admin/alerts/node/change_form.html
git commit -m "refactor(admin): node overview carries a tone, not a hex"
```

---

### Task 9: Migrate `apps/notify/admin.py` and `config/dashboard.py`

**Files:**
- Modify: `apps/notify/admin.py:102`
- Modify: `config/dashboard.py:27-28`
- Test: `apps/notify/_tests/test_admin.py`, `config/_tests/test_dashboard.py`

`apps/notify/admin.py:102` becomes:

```python
                            tinted(format_html("&#9888; cannot deliver: {}", reason), "warning"),
```

`config/dashboard.py:27-28` is a `<pre>` with `var(--body-bg, #f8f9fa)` fallbacks. Replace
the whole inline style with `class="ops-pre"` and add to `ops.css`:

```css
.ops-pre {
  background: var(--darkened-bg);
  color: var(--body-fg);
  border: 1px solid var(--hairline-color);
  border-radius: var(--ops-radius-sm);
  padding: var(--ops-space-3);
  font-family: var(--ops-mono);
  font-size: 12px;
  overflow-x: auto;
}
```

Steps 1-5 as before. Commit message: `refactor(admin): move notify and dashboard markers onto tones`.

---

### Task 10: Migrate `templates/admin/policy_overview.html`

**Files:**
- Modify: `templates/admin/policy_overview.html` (lines 9, 46, 49, 52, 60, 76 and the
  inline `style=` attributes on the tables)
- Test: `config/_tests/test_policy_overview_view.py` lines 56, 66, 114, 115, 145

**Step 1: Rewrite the five coupled assertions**

```python
    assert "ops-badge--ok" in body            # was: assert "#28a745" in body
    assert "ops-badge--ok" not in body        # was: assert "#28a745" not in body
    assert "ops-tint--warning" in body        # was the literal span with #b26a00
    assert "ops-badge--muted" in body         # was: assert "#6c757d" in body
```

**Step 2: Run to verify they fail.**

**Step 3: Replace the template's inline styles**

The three status spans at lines 46, 49, 52 collapse to one, since the view already knows
the status. Give `build_policy_overview` rows a `tone` the same way Task 8 did, or map in
the template if the row already carries a status string:

```html
<span class="ops-badge ops-badge--{{ row.tone }}">{{ row.status_label }}</span>
```

Line 60 becomes:

```html
<span class="ops-tint ops-tint--warning">&#9888; {{ row.why }}</span>
```

Lines 9 and 76 (`style="color:var(--body-quiet-color, #666); font-size:13px; ..."`) become
`class="ops-note"`, with:

```css
.ops-note { color: var(--body-quiet-color); font-size: 13px; margin: var(--ops-space-1) 0 var(--ops-space-4); }
```

The inline table styles become `class="ops-table"`:

```css
.ops-table { width: 100%; border-collapse: collapse; }
.ops-table th { text-align: left; font-weight: 600; color: var(--body-quiet-color); }
.ops-table th, .ops-table td { padding: var(--ops-space-2) var(--ops-space-3); border-bottom: 1px solid var(--hairline-color); }
.ops-scroll { overflow-x: auto; }
```

**The autoescaping comment at the top of this template must stay.** `instance_id`,
`hostname` and checker names arrive over a webhook.

**Steps 4, 5** as before. Commit message:
`refactor(admin): policy overview uses classes, not inline hexes`.

---

### Task 11: Migrate `templates/admin/alerts/node/change_form.html`

**Files:**
- Modify: `templates/admin/alerts/node/change_form.html` (lines 30, 34, 38, 170, 180, 188
  and the inline table/module styles)
- Test: `apps/alerts/_tests/test_node_admin.py`

The three freshness spans at 30/34/38 differ only in hex, so the whole `{% if %}` chain
collapses. `freshness_status` is already `ok` / `warn` / else, so map it to a tone in
`node_overview.py` (`freshness_tone`: `ok` → `ok`, `warn` → `warning`, else `muted`) and
render:

```html
<span class="ops-badge ops-badge--{{ node_overview.identity.freshness_tone }}">
  {{ node_overview.identity.freshness_label }}</span>
```

Keep the existing comment explaining why there are three states, not two. That is
load-bearing context, it records a fixed bug.

Lines 170, 180, 188 become `class="ops-tint ops-tint--warning"`, and for 188:

```css
.ops-callout--warning { border-left: 4px solid var(--ops-warning-fg); padding-left: var(--ops-space-3); }
```

**Do not touch** the `{% comment %}` block at the top. It documents why this template
extends `django_object_actions/change_form.html`, and getting that wrong silently drops the
"Re-evaluate open alerts" button.

Steps as before. Commit message: `refactor(admin): node change form uses classes, not inline hexes`.

---

### Task 12: The guard test

**Files:**
- Create: `config/_tests/test_no_inline_colour.py`

**Step 1: Write the test**

```python
"""Colour lives in ops.css. This is what keeps it there.

Without this, the next person adding a status column reaches for
format_html('<span style="color:#dc3545">') because that is what the
neighbouring code used to look like.
"""

import re
from pathlib import Path

from django.conf import settings

HEX = re.compile(r"(?:color|background|background-color)\s*:\s*#[0-9a-fA-F]{3,8}")

SCANNED = [
    *(Path(settings.BASE_DIR) / "apps").glob("*/admin.py"),
    *(Path(settings.BASE_DIR) / "apps").glob("*/node_overview.py"),
    *(Path(settings.BASE_DIR) / "templates" / "admin").rglob("*.html"),
    Path(settings.BASE_DIR) / "config" / "dashboard.py",
]


def test_no_inline_colour_outside_the_stylesheet():
    offenders = {
        str(path.relative_to(settings.BASE_DIR)): HEX.findall(path.read_text())
        for path in SCANNED
        if HEX.search(path.read_text())
    }
    assert offenders == {}, f"inline colour belongs in ops.css: {offenders}"


def test_the_guard_actually_scans_something():
    # A glob that silently matches nothing would make the test above vacuous.
    assert len(SCANNED) > 10
```

**Step 2: Run it**

```bash
uv run pytest config/_tests/test_no_inline_colour.py -v
```

Expected: PASS. If it fails, Tasks 3-11 missed a site. Fix the site, not the regex.

**Step 3: Commit**

```bash
git add config/_tests/test_no_inline_colour.py
git commit -m "test(admin): guard against inline colour creeping back"
```

---

### Task 13: Style changelists and filters

**Files:**
- Modify: `static/admin/css/ops.css`

No tests. This is paint. Verification is visual, in Step 3.

**Step 1: Append to `ops.css`**

```css
#changelist table { border-collapse: collapse; }

#changelist thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--darkened-bg);
  border-bottom: 1px solid var(--border-color);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

#changelist tbody tr { background: transparent; }
#changelist tbody td, #changelist tbody th {
  border-bottom: 1px solid var(--hairline-color);
  padding: var(--ops-space-2) var(--ops-space-3);
}
#changelist tbody tr:hover { background: var(--selected-row); }

/* Counts and durations read as columns of digits, not prose. */
#changelist td.field-duration_ms,
#changelist td.field-alert_count_display,
#changelist td.field-firing_alert_count_display,
#changelist td.field-pipeline_runs_display {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

#changelist-filter {
  background: var(--module-bg);
  border-left: 1px solid var(--hairline-color);
}
#changelist-filter h3 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--body-quiet-color);
}
#changelist-filter li.selected { border-left: 3px solid var(--accent); }

#toolbar {
  display: flex;
  align-items: center;
  gap: var(--ops-space-3);
  background: var(--module-bg);
  border: 1px solid var(--hairline-color);
  border-radius: var(--ops-radius);
  padding: var(--ops-space-2) var(--ops-space-3);
}
#toolbar form#changelist-search { display: flex; gap: var(--ops-space-2); align-items: center; }

.paginator {
  border-top: 1px solid var(--hairline-color);
  padding-top: var(--ops-space-3);
  font-size: 12px;
}
```

**Step 2:** run `uv run pytest -q` to confirm nothing regressed.

**Step 3: Eyeball**

```bash
uv run python manage.py runserver
```

Visit `/admin/alerts/incident/`, `/admin/alerts/alert/`, `/admin/orchestration/pipelinerun/`
and `/admin/checkers/checkrun/`. Check in **both** themes:

- the header row stays put when you scroll
- filter sidebar is readable, selected filters are obvious
- trace/run IDs are monospace
- no unstyled white boxes

**Step 4: Commit**

```bash
git add static/admin/css/ops.css
git commit -m "feat(admin): restyle changelists and the filter panel"
```

---

### Task 14: Style forms and buttons

**Files:**
- Modify: `static/admin/css/ops.css`

**Step 1: Append**

```css
.button, input[type=submit], input[type=button], .submit-row input, a.button {
  border-radius: var(--ops-radius-sm);
  border: 1px solid var(--border-color);
  padding: var(--ops-space-2) var(--ops-space-4);
  font-weight: 600;
  font-size: 13px;
  line-height: 1.4;
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
}

.button:focus-visible, input[type=submit]:focus-visible, a.button:focus-visible,
input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.submit-row input[type=submit].default { background: var(--accent); border-color: var(--accent); }
.submit-row a.deletelink { background: var(--ops-critical-bg); color: var(--ops-critical-fg); border-color: transparent; }

.module {
  background: var(--module-bg);
  border: 1px solid var(--hairline-color);
  border-radius: var(--ops-radius);
  overflow: hidden;
}
.module h2, .module caption {
  background: var(--darkened-bg);
  color: var(--body-quiet-color);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: var(--ops-space-2) var(--ops-space-3);
}

input[type=text], input[type=password], input[type=email], input[type=url],
input[type=number], textarea, select {
  background: var(--body-bg);
  color: var(--body-fg);
  border: 1px solid var(--border-color);
  border-radius: var(--ops-radius-sm);
  padding: var(--ops-space-2);
}

/* django_object_actions puts its buttons here. */
.object-tools a, ul.object-tools li a {
  border-radius: var(--ops-radius-sm);
  background: var(--darkened-bg);
  color: var(--body-fg);
  border: 1px solid var(--border-color);
  text-transform: none;
  letter-spacing: 0;
  font-weight: 600;
}
.object-tools a:hover { background: var(--selected-row); }

/* django_json_widget: a white editor in a dark page is the worst offender. */
.jsoneditor { border-color: var(--border-color) !important; }
.jsoneditor-menu { background: var(--darkened-bg) !important; border-bottom-color: var(--border-color) !important; }
.jsoneditor-outer, .ace_editor, .jsoneditor textarea.jsoneditor-text {
  background: var(--body-bg) !important;
  color: var(--body-fg) !important;
  font-family: var(--ops-mono) !important;
}
```

The `!important` on the JSON widget rules is deliberate: `jsoneditor.min.css` is a vendor
bundle loaded by the widget itself and ships high-specificity rules. Note that in a comment
in the file.

**Step 2:** `uv run pytest -q`.

**Step 3: Eyeball.** Visit `/admin/alerts/node/<id>/change/`,
`/admin/orchestration/pipelinedefinition/add/` (the JSON widget), and an Incident change
form. Confirm the "Re-evaluate open alerts" and "Policy overview" buttons still render and
still work.

**Step 4: Commit** — `feat(admin): restyle forms, buttons and the JSON widget`.

---

### Task 15: Fold the dashboard and map styles into `ops.css`

**Files:**
- Modify: `templates/admin/dashboard.html` (delete the `<style>` block, lines ~5-260)
- Modify: `templates/admin/map.html` (delete its `<style>` block)
- Modify: `static/admin/css/ops.css`
- Test: `config/_tests/test_dashboard_render.py`, `config/_tests/test_netmap.py`

**Step 1:** Move both `<style>` blocks into `ops.css` verbatim, then replace their hardcoded
values with tokens. The dashboard already uses semantic class names
(`readiness-card readiness-{{ r.status }}`, `severity-badge critical`), so the selectors
carry over unchanged. Prefix nothing, the selectors are already scoped under `#dashboard`,
`#readiness` and `#netmap`.

Retarget in particular:

- `.metric-subtitle { color: #666 }` → `var(--body-quiet-color)`
- `.severity-badge.critical/.warning/.info` → the `--ops-*-fg` / `--ops-*-bg` pairs
- `.readiness-*` → the same five tones, plus a `border-left: 3px solid` status edge
- `.progress-bar.success/.warning/.error` → `--ops-ok-fg` / `--ops-warning-fg` / `--ops-critical-fg`
- `map.html`'s `var(--hairline-color, #ddd)` fallbacks → drop the fallback, the var always exists now

**Step 2:** Add to `test_dashboard_render.py`:

```python
def test_dashboard_carries_no_inline_style_block(admin_client):
    assert "<style>" not in admin_client.get("/admin/").content.decode()
```

**Step 3:** `uv run pytest config/ -v`.

**Step 4: Eyeball** `/admin/` and `/admin/map/` in both themes.

**Step 5: Commit** — `refactor(admin): move dashboard and map styles into ops.css`.

---

### Task 16: Style the header, breadcrumbs and section sidebar

**Files:**
- Modify: `static/admin/css/ops.css`

**Step 1: Append**

```css
#header {
  padding: var(--ops-space-3) var(--ops-space-6);
  border-bottom: 1px solid var(--hairline-color);
}
#site-name a { font-weight: 700; letter-spacing: -0.01em; }

div.breadcrumbs {
  font-size: 12px;
  padding: var(--ops-space-2) var(--ops-space-6);
  border-bottom: 1px solid var(--hairline-color);
}

/* The get_app_list() sections from config/admin.py SECTION_MAP. */
#nav-sidebar .app > th,
#nav-sidebar caption a {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--body-quiet-color);
}
#nav-sidebar tr.current-app th,
#nav-sidebar th.current-model,
#nav-sidebar td.current-model {
  border-left: 3px solid var(--accent);
  background: var(--selected-row);
}
#nav-sidebar a { padding-top: var(--ops-space-1); padding-bottom: var(--ops-space-1); }
```

**Step 2:** `uv run pytest -q`.

**Step 3: Eyeball.** Confirm all three sections (Operations, Configuration, History & Audit)
plus "Other" render as section headers, and the current page is highlighted. Confirm the
"Hub-side policy" and "Network map" `SECTION_LINKS` entries still appear in the sidebar and
on the dashboard's Navigate card.

**Step 4: Commit** — `feat(admin): restyle the header, breadcrumbs and section sidebar`.

---

### Task 17: Full verification

**REQUIRED SUB-SKILL:** Use superpowers:verification-before-completion.

**Step 1: Full suite and coverage**

```bash
uv run coverage run -m pytest && uv run coverage report
```

Expected: all green, 100% branch coverage on every changed Python file.

**Step 2: Lint and format**

```bash
uv run black . --check
uv run ruff check .
uv run bandit -r apps/ config/ -c pyproject.toml
```

**Step 3: Django checks and static collection**

```bash
uv run python manage.py check
uv run python manage.py collectstatic --noinput --dry-run
```

`collectstatic` must find `admin/css/ops.css`. If it does not, `STATICFILES_DIRS` is wrong.

**Step 4: The visual pass**

```bash
uv run python manage.py runserver
```

In **both** light and dark, walk:

| Page | What to confirm |
|---|---|
| `/admin/` | readiness cards tinted, no `<style>` block in source, badges pill-shaped |
| `/admin/alerts/incident/` | sticky header, filter panel, severity and status badges |
| `/admin/alerts/alert/` | same, plus monospace fingerprints |
| `/admin/alerts/node/` | "Policy overview" button present in object-tools |
| `/admin/alerts/node/<id>/change/` | freshness badge, "Re-evaluate open alerts" button present and working, checker table readable |
| `/admin/policy/` | badges and warning markers, table readable |
| `/admin/map/` | lane cards readable, no fallback-hex artefacts |
| `/admin/orchestration/pipelinedefinition/add/` | JSON editor is dark, monospace, readable |
| `/admin/orchestration/pipelinerun/` | stage strip glyphs tinted correctly |

**Step 5: Final commit and PR**

```bash
git add -A && git commit -m "feat(admin): finish the ops skin"
git push -u origin feat/admin-ops-skin
gh pr create --fill
```

Never push to `main`.

## Acceptance criteria

1. `uv run pytest` green, 100% branch coverage on changed Python.
2. `config/_tests/test_no_inline_colour.py` passes, so no literal status hex remains in
   `apps/*/admin.py`, `apps/*/node_overview.py`, `config/dashboard.py` or `templates/admin/`.
3. Every page in the Task 17 table renders correctly in both themes.
4. The `django_object_actions` buttons still appear and still work on the Node changelist
   and change form.
5. No change to any admin URL, registered model, field set or action.

{% endraw %}
