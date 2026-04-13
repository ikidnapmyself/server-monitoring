---
title: "Auth Enabled by Default Design"
parent: Plans
---

# Enable API Key Authentication by Default

## Problem

`API_KEY_AUTH_ENABLED` defaults to `"0"` (disabled). Any deployment that doesn't explicitly set `API_KEY_AUTH_ENABLED=1` exposes all API endpoints (`/alerts/`, `/notify/`, `/orchestration/`, `/intelligence/`) without authentication. No other auth layer exists on these paths.

## Solution

1. **Flip the default** in `config/settings.py` from `"0"` to `"1"`
2. **Fix 2 test files** that hit API paths without managing the setting — add `@override_settings(API_KEY_AUTH_ENABLED=False)`
3. **Add Django system check** that warns when `API_KEY_AUTH_ENABLED` is off in a non-DEBUG environment
4. **Update `.env.sample`** — add `API_KEY_AUTH_ENABLED=0` so devs who copy the sample get a working dev setup
5. **Update docs** — `docs/Security.md` and `docs/Deployment.md` to reflect the new default

## Affected Files

| File | Change |
|------|--------|
| `config/settings.py:229` | Default `"0"` → `"1"` |
| `apps/notify/_tests/test_views.py` | Add `@override_settings(API_KEY_AUTH_ENABLED=False)` |
| `apps/alerts/_tests/views/test_webhook.py` | Add `@override_settings(API_KEY_AUTH_ENABLED=False)` |
| `config/checks.py` (or equivalent) | New system check: warn if auth disabled + not DEBUG |
| `.env.sample` | Add `API_KEY_AUTH_ENABLED=0` |
| `docs/Security.md` | Update default value documentation |
| `docs/Deployment.md` | Update env var table |

## System Check

A new Django system check (`security.W001`) warns at startup when `API_KEY_AUTH_ENABLED=False` and `DEBUG=False`. This catches production misconfigurations without blocking development.

## Test Plan

- Existing auth middleware tests already use `@override_settings(API_KEY_AUTH_ENABLED=True)` — unaffected
- Rate limit tests already use `@override_settings(API_KEY_AUTH_ENABLED=False)` — unaffected
- 2 test files get the override added — verified they pass
- New test for the system check itself
- Full test suite passes