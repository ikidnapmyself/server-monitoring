---
title: "2026-03-07 Config Directory Cleanup Design"
parent: Plans
---

# Config Directory Cleanup Design

## Context

After PR #59 (`refactor/env-cleanup`) removed env-var-based checker/notify settings from
`config/settings.py`, the `config/` directory is leaner but still has structural and coverage gaps:

- **0% coverage** on `asgi.py` and `wsgi.py`
- **96% coverage** on `settings.py` (missing `RuntimeError` branch on line 36)
- **94% coverage** on `env.py` (missing dev-env branch)
- **No `_tests/` directory** — zero dedicated tests for any config module
- **140-line `admin.py`** mixes admin site config with dashboard query logic

## Design

### 1. Extract Dashboard Logic

Move `prettify_json()` and `_get_dashboard_context()` from `config/admin.py` into a new
`config/dashboard.py`. Rename `_get_dashboard_context` to `get_dashboard_context` (public API).

`config/admin.py` becomes a thin wrapper that imports and delegates.

### 2. Test Coverage

Create `config/_tests/` with:

| File | Covers |
|------|--------|
| `test_settings.py` | Missing `DJANGO_SECRET_KEY` raises `RuntimeError` |
| `test_env.py` | `load_env()` paths, `_should_load_dev_env()` with `DJANGO_ENV=dev` |
| `test_entrypoints.py` | `asgi.py` and `wsgi.py` export callable `application` |
| `test_dashboard.py` | `get_dashboard_context()` keys, `prettify_json()` output |
| `test_admin.py` | `MonitoringAdminSite` delegates to `get_dashboard_context` |

Target: 100% branch coverage for all `config/` files.

### 3. No Changes To

- Settings values or comments
- `.env.sample`
- `celery.py`, `urls.py`, `apps.py`, `__init__.py`