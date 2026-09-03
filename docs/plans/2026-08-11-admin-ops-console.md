---
title: "2026-08-11 Admin Ops-Console Implementation Plan"
parent: Plans
---

{% raw %}
# Admin Ops-Console Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the admin a friendlier operations console — add a configuration-readiness panel to the existing dashboard and regroup all models into operator-facing sections (Operations / Configuration / History & Audit).

**Architecture:** Extend the existing `MonitoringAdminSite` (`config/admin.py`) and its dashboard context (`config/dashboard.py`, `templates/admin/dashboard.html`). A `build_readiness()` aggregator feeds status cards; a `get_app_list()` override re-buckets registered models (reusing Django's permission-filtered model dicts) for both the sidebar nav and a grouped grid rendered on the dashboard. No new dependency, endpoint, or model.

**Tech Stack:** Django admin (`AdminSite.get_app_list`, custom `index_template`), Django ORM aggregates, `format_html`, pytest + pytest-django. `uv run` for everything.

**Conventions (AGENTS.md):** absolute imports; 100-char lines; Black + Ruff; 100% branch coverage on changed code; escape all admin HTML via `format_html`; theme-aware via existing `var(--body-bg/--body-fg/--hairline-color)` CSS vars; no external assets. Design: `docs/plans/2026-08-11-admin-ops-console-design.md`.

**Verified facts:**
- `NotificationChannel.is_active` (bool), `.name`, `.driver`. `IntelligenceProvider.is_active` (bool, only one active). `PreflightRun.overall_status` ("ok"/"warn"/"error"), `.created_at`, `.passed/.warnings/.errors`. `Node.last_seen` (auto_now), `.count()`. `InboxItem` proxy (PENDING/PROCESSING) with `is_stuck()`; stuck via query = PROCESSING + `updated_at < now - DEFAULT_STALE_MINUTES` (constant in `apps/orchestration/inbox.py`).
- Registered models (app_label.model_name): `alerts.incident/alert/node/alerthistory`, `orchestration.inboxitem/pipelinerun/pipelinedefinition/stageexecution`, `notify.notificationchannel`, `intelligence.intelligenceprovider/analysisrun`, `checkers.checkrun/preflightrun`, `config_app.apikey`, `auth.user/group`.
- `templates/admin/dashboard.html` extends `admin/index.html`, overrides `{% block content %}` (line 4), so the grouped grid must be rendered inside that block; `app_list` is available in the index context.

---

## Phase 1 — Readiness aggregator

### Task 1.1: `build_readiness()` — the five signals

**Files:**
- Modify: `config/dashboard.py` (add `build_readiness()` + a status constant)
- Test: `config/_tests/test_dashboard_readiness.py` (create; add `__init__.py` if `config/_tests/` lacks one — check first)

**Step 1: Write the failing tests**

```python
# config/_tests/test_dashboard_readiness.py
import pytest
from django.urls import reverse

from config.dashboard import build_readiness


def _by_key(readiness):
    return {r["key"]: r for r in readiness}


@pytest.mark.django_db
def test_channels_error_when_none_active():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="c1", driver="slack", is_active=False)
    r = _by_key(build_readiness())["channels"]
    assert r["status"] == "error"
    assert r["url"] == reverse("admin:notify_notificationchannel_changelist")


@pytest.mark.django_db
def test_channels_warn_when_some_inactive():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)
    NotificationChannel.objects.create(name="b", driver="email", is_active=False)
    assert _by_key(build_readiness())["channels"]["status"] == "warn"


@pytest.mark.django_db
def test_channels_ok_when_all_active():
    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)
    assert _by_key(build_readiness())["channels"]["status"] == "ok"


@pytest.mark.django_db
def test_provider_error_when_none_active_else_ok():
    from apps.intelligence.models import IntelligenceProvider

    assert _by_key(build_readiness())["provider"]["status"] == "error"
    IntelligenceProvider.objects.create(name="p", provider="anthropic", is_active=True)
    assert _by_key(build_readiness())["provider"]["status"] == "ok"


@pytest.mark.django_db
def test_preflight_neutral_then_maps_status():
    from apps.checkers.models import PreflightRun

    assert _by_key(build_readiness())["preflight"]["status"] == "neutral"
    PreflightRun.objects.create(overall_status="warn")
    assert _by_key(build_readiness())["preflight"]["status"] == "warn"


@pytest.mark.django_db
def test_inbox_ok_backlog_stuck():
    from datetime import timedelta

    from django.utils import timezone

    from apps.orchestration.models import PipelineRun, PipelineStatus

    # empty -> ok
    assert _by_key(build_readiness())["inbox"]["status"] == "ok"
    # backlog -> warn
    PipelineRun.objects.create(trace_id="t", run_id="p1", status=PipelineStatus.PENDING)
    assert _by_key(build_readiness())["inbox"]["status"] == "warn"
    # stuck PROCESSING -> error
    run = PipelineRun.objects.create(trace_id="t", run_id="p2", status=PipelineStatus.PROCESSING)
    PipelineRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    assert _by_key(build_readiness())["inbox"]["status"] == "error"


@pytest.mark.django_db
def test_nodes_neutral_then_recent_ok():
    from apps.alerts.models import Node

    assert _by_key(build_readiness())["nodes"]["status"] == "neutral"
    Node.objects.create(instance_id="agent-1")  # last_seen auto_now -> recent
    assert _by_key(build_readiness())["nodes"]["status"] == "ok"
```

**Step 2: Run to verify they fail**

Run: `uv run pytest config/_tests/test_dashboard_readiness.py -q`
Expected: FAIL — `ImportError: build_readiness`.

**Step 3: Implement `build_readiness()`**

Add to `config/dashboard.py`:

```python
def build_readiness():
    """Configuration-readiness signals for the dashboard.

    Each entry: {key, label, status (ok|warn|error|neutral), detail, url}.
    Pure read-aggregation; safe on empty tables.
    """
    from datetime import timedelta

    from django.urls import reverse
    from django.utils import timezone

    from apps.alerts.models import Node
    from apps.checkers.models import PreflightRun
    from apps.intelligence.models import IntelligenceProvider
    from apps.notify.models import NotificationChannel
    from apps.orchestration.inbox import DEFAULT_STALE_MINUTES
    from apps.orchestration.models import PipelineRun, PipelineStatus

    now = timezone.now()
    out = []

    # Channels
    total = NotificationChannel.objects.count()
    active = NotificationChannel.objects.filter(is_active=True).count()
    if active == 0:
        c_status, detail = "error", "No active channel — alerts will not be delivered"
    elif active < total:
        c_status, detail = "warn", f"{active}/{total} channels active"
    else:
        c_status, detail = "ok", f"{active} channel(s) active"
    out.append({
        "key": "channels", "label": "Notification channels", "status": c_status,
        "detail": detail, "url": reverse("admin:notify_notificationchannel_changelist"),
    })

    # LLM provider
    p_active = IntelligenceProvider.objects.filter(is_active=True).count()
    out.append({
        "key": "provider", "label": "LLM provider",
        "status": "ok" if p_active else "error",
        "detail": "Active provider set" if p_active
        else "No active provider — analysis falls back to 'no AI'",
        "url": reverse("admin:intelligence_intelligenceprovider_changelist"),
    })

    # Preflight
    latest = PreflightRun.objects.order_by("-created_at").first()
    if latest is None:
        pf_status, detail = "neutral", "Never run"
    else:
        pf_status = latest.overall_status if latest.overall_status in {"ok", "warn", "error"} \
            else "neutral"
        detail = f"{latest.passed} ok / {latest.warnings} warn / {latest.errors} error"
    out.append({
        "key": "preflight", "label": "Preflight", "status": pf_status, "detail": detail,
        "url": reverse("admin:checkers_preflightrun_changelist"),
    })

    # Inbox
    pending = PipelineRun.objects.filter(status=PipelineStatus.PENDING).count()
    stuck = PipelineRun.objects.filter(
        status=PipelineStatus.PROCESSING,
        updated_at__lt=now - timedelta(minutes=DEFAULT_STALE_MINUTES),
    ).count()
    if stuck:
        i_status, detail = "error", f"{stuck} stuck run(s)"
    elif pending:
        i_status, detail = "warn", f"{pending} pending"
    else:
        i_status, detail = "ok", "Drained"
    out.append({
        "key": "inbox", "label": "Inbox", "status": i_status, "detail": detail,
        "url": reverse("admin:orchestration_inboxitem_changelist"),
    })

    # Nodes
    total_nodes = Node.objects.count()
    recent = Node.objects.filter(last_seen__gte=now - timedelta(minutes=15)).count()
    if total_nodes == 0:
        n_status, detail = "neutral", "No nodes seen"
    elif recent:
        n_status, detail = "ok", f"{recent}/{total_nodes} seen recently"
    else:
        n_status, detail = "warn", "No node seen in 15 min"
    out.append({
        "key": "nodes", "label": "Nodes", "status": n_status, "detail": detail,
        "url": reverse("admin:alerts_node_changelist"),
    })

    return out
```

> Decision to confirm during review: "channels warn when some inactive" is implemented verbatim from the design. If it proves noisy (inactive channels are often intentional), collapse to ok/error.

**Step 4: Run to verify pass**

Run: `uv run pytest config/_tests/test_dashboard_readiness.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add config/dashboard.py config/_tests/test_dashboard_readiness.py
git commit -m "feat(admin): readiness aggregator for dashboard (channels/provider/preflight/inbox/nodes)"
```

### Task 1.2: Wire readiness into the dashboard context

**Files:**
- Modify: `config/dashboard.py` (`get_dashboard_context()` return dict)
- Test: append to `config/_tests/test_dashboard_readiness.py`

**Step 1: Failing test** — assert `"readiness" in get_dashboard_context()` and it's a non-empty list of dicts with the five keys.

**Step 2:** run → fail.

**Step 3:** add `"readiness": build_readiness(),` to the returned dict in `get_dashboard_context()`.

**Step 4:** run → pass.

**Step 5: Commit**

```bash
git add config/dashboard.py config/_tests/test_dashboard_readiness.py
git commit -m "feat(admin): expose readiness in dashboard context"
```

---

## Phase 2 — Task-oriented navigation (`get_app_list`)

### Task 2.1: `SECTION_MAP` + `get_app_list` override

**Files:**
- Modify: `config/admin.py` (`MonitoringAdminSite`)
- Test: `config/_tests/test_admin_site.py` (create)

**Step 1: Write the failing tests**

```python
# config/_tests/test_admin_site.py
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from config.admin import MonitoringAdminSite


@pytest.fixture
def su(db):
    return get_user_model().objects.create_superuser("admin", "a@b.co", "x")


def _sections(su):
    from django.contrib import admin

    req = RequestFactory().get("/admin/")
    req.user = su
    # Use the configured site instance so all models are registered.
    site = admin.site
    return {a["name"]: [m["object_name"] for m in a["models"]] for a in site.get_app_list(req)}


@pytest.mark.django_db
def test_sections_are_operator_facing(su):
    sections = _sections(su)
    assert set(sections) >= {"Operations", "Configuration", "History & Audit"}
    assert "Incident" in sections["Operations"]
    assert "Node" in sections["Operations"]           # Nodes under Operations
    assert "NotificationChannel" in sections["Configuration"]
    assert "CheckRun" in sections["History & Audit"]


@pytest.mark.django_db
def test_operations_order_is_explicit(su):
    ops = _sections(su)["Operations"]
    assert ops.index("Incident") < ops.index("Alert") < ops.index("PipelineRun")


@pytest.mark.django_db
def test_unlisted_model_falls_into_other(su):
    # auth.User/Group are not in the curated map -> Configuration (access) or Other.
    sections = _sections(su)
    all_models = [m for models in sections.values() for m in models]
    assert "User" in all_models and "Group" in all_models  # never silently dropped
```

**Step 2: Run to verify fail**

Run: `uv run pytest config/_tests/test_admin_site.py -q`
Expected: FAIL (default grouping is per-app: "Alerts", "Checkers", ...).

**Step 3: Implement**

In `config/admin.py`, add the map + override on `MonitoringAdminSite`:

```python
from django.utils.text import slugify

# Ordered section -> ordered list of "app_label.model_name" (lowercase).
SECTION_MAP = {
    "Operations": [
        "alerts.incident", "alerts.alert",
        "orchestration.inboxitem", "orchestration.pipelinerun", "alerts.node",
    ],
    "Configuration": [
        "notify.notificationchannel", "intelligence.intelligenceprovider",
        "orchestration.pipelinedefinition", "config_app.apikey",
        "auth.user", "auth.group",
    ],
    "History & Audit": [
        "checkers.checkrun", "checkers.preflightrun", "intelligence.analysisrun",
        "alerts.alerthistory", "orchestration.stageexecution",
    ],
}


class MonitoringAdminSite(AdminSite):
    ...

    def get_app_list(self, request, app_label=None):
        # Per-app index pages keep Django's native behaviour.
        if app_label is not None:
            return super().get_app_list(request, app_label)

        default = super().get_app_list(request, app_label)
        by_key = {}
        for app in default:
            for model in app["models"]:
                by_key[f"{app['app_label']}.{model['object_name'].lower()}"] = model

        sections, used = [], set()
        for name, keys in SECTION_MAP.items():
            models = [by_key[k] for k in keys if k in by_key]
            used.update(k for k in keys if k in by_key)
            if models:
                sections.append({
                    "name": name,
                    "app_label": slugify(name),
                    "app_url": models[0]["admin_url"],  # header links to first model
                    "has_module_perms": True,
                    "models": models,
                })

        leftover = [m for k, m in by_key.items() if k not in used]
        if leftover:
            sections.append({
                "name": "Other", "app_label": "other",
                "app_url": leftover[0]["admin_url"], "has_module_perms": True,
                "models": leftover,
            })
        return sections
```

> Note: `auth.user`/`auth.group` are placed in Configuration (access setup). The "Other" bucket is a safety net so any future unregistered-in-map model still appears.

**Step 4: Run to verify pass**

Run: `uv run pytest config/_tests/test_admin_site.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add config/admin.py config/_tests/test_admin_site.py
git commit -m "feat(admin): regroup models into Operations/Configuration/History sections"
```

### Task 2.2: Permission filtering + empty-section hiding

**Files:**
- Test: append to `config/_tests/test_admin_site.py`

**Step 1: Failing tests** — with a non-superuser staff user having only (say) `notify.view_notificationchannel`, assert `get_app_list` returns only sections/models they can see, and sections with zero visible models are absent (Django's `super().get_app_list()` already filters by perms, so this should pass once we trust its dicts — the test locks the behaviour in).

**Step 2–4:** run fail (if needed) → confirm the override preserves filtering → pass. (No code change expected beyond Task 2.1; if a section leaks, fix by only including `by_key` entries, which are already perm-filtered.)

**Step 5: Commit**

```bash
git add config/_tests/test_admin_site.py
git commit -m "test(admin): section nav respects permissions and hides empty sections"
```

---

## Phase 3 — Rendering

### Task 3.1: Readiness cards + grouped grid on the dashboard

**Files:**
- Modify: `templates/admin/dashboard.html` (inside `{% block content %}`)
- Test: `config/_tests/test_dashboard_render.py` (create) — render the index as a superuser via the test client.

**Step 1: Write the failing test**

```python
# config/_tests/test_dashboard_render.py
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
def test_dashboard_renders_readiness_and_sections(client):
    get_user_model().objects.create_superuser("admin", "a@b.co", "x")
    client.login(username="admin", password="x")

    from apps.notify.models import NotificationChannel

    NotificationChannel.objects.create(name="a", driver="slack", is_active=True)

    resp = client.get(reverse("admin:index"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Readiness" in body
    assert "Notification channels" in body
    assert 'class="readiness-card' in body  # status-classed card
    # grouped nav grid rendered on the dashboard content
    assert "Operations" in body and "Configuration" in body
```

**Step 2: Run to verify fail**

Run: `uv run pytest config/_tests/test_dashboard_render.py -q`
Expected: FAIL (no readiness markup / no section grid).

**Step 3: Implement template**

Inside `{% block content %}` of `templates/admin/dashboard.html`, above the existing activity cards, add a readiness row driven by the `readiness` context list; use the status value as a CSS class and the existing `var(--*)` colors for the ok/warn/error/neutral accents. Sketch:

```django
<div id="readiness">
  <h2>Readiness</h2>
  <div class="dashboard-row">
    {% for r in readiness %}
      <a class="readiness-card readiness-{{ r.status }}" href="{{ r.url }}">
        <strong>{{ r.label }}</strong>
        <span class="readiness-status">{{ r.status|upper }}</span>
        <span class="readiness-detail">{{ r.detail }}</span>
      </a>
    {% endfor %}
  </div>
</div>
```

Add CSS in the existing `<style>` block: `.readiness-ok{...}` green, `.readiness-warn{...}` amber, `.readiness-error{...}` red, `.readiness-neutral{...}` muted, all using `var(--body-fg)` etc. so they adapt to light/dark. Then, near the bottom of the block, render the grouped grid from `app_list`:

```django
<div id="nav-sections">
  {% for app in app_list %}
    <div class="dashboard-card">
      <h2>{{ app.name }}</h2>
      <ul>
        {% for model in app.models %}
          <li><a href="{{ model.admin_url }}">{{ model.name }}</a></li>
        {% endfor %}
      </ul>
    </div>
  {% endfor %}
</div>
```

All values are auto-escaped by the Django template engine (no `|safe`).

**Step 4: Run to verify pass**

Run: `uv run pytest config/_tests/test_dashboard_render.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add templates/admin/dashboard.html config/_tests/test_dashboard_render.py
git commit -m "feat(admin): render readiness cards + grouped nav on the dashboard"
```

---

## Phase 4 — Verification & docs

### Task 4.1: Full gate

Run:
```bash
uv run coverage run -m pytest && uv run coverage report
uv run black . --check
uv run ruff check .
uv run bandit -r apps/ config/ -c pyproject.toml
uv run python manage.py makemigrations --check --dry-run
```
Expected: all green; 100% branch coverage on changed lines; no new migrations (this feature adds none).

### Task 4.2: Docs

Update `bin/AGENTS.md`/`config`-level notes (or the top-level `AGENTS.md` admin section) to mention: the dashboard readiness panel and the task-oriented section grouping (`SECTION_MAP` in `config/admin.py`). Commit:

```bash
git commit -m "docs(admin): document readiness panel + section grouping"
```

## Acceptance criteria

- Dashboard shows five readiness cards with correct ok/warn/error/neutral status and working deep links.
- Sidebar nav + dashboard grid group models into Operations / Configuration / History & Audit (Nodes under Operations); permission-safe; empty sections hidden; unlisted models never dropped.
- No new dependency/endpoint/model; theme-aware; CI green; 100% branch coverage on changed lines.
{% endraw %}
