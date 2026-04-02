---
title: "2026-04-02 Set Production Helper Design"
parent: Plans
---

# Set Production Helper Design

**Date:** 2026-04-02
**Status:** Approved

## Problem

Converting a dev install to production requires manually editing multiple `.env` keys and re-syncing dependencies. There is no single command to do this, and the health check does not warn when DEBUG is enabled in production.

## Goal

1. Create `bin/set_production.sh` — converts a dev environment to production
2. Add a health check warning when `DJANGO_DEBUG=1` in prod, pointing to the new script

## Design

### `bin/set_production.sh`

Sources `bin/lib/` helpers. Performs these steps:

1. Set `DJANGO_ENV=prod` via `dotenv_set`
2. Set `DJANGO_DEBUG=0` via `dotenv_set`
3. Check `DJANGO_SECRET_KEY` — if empty, generate with python3 (or prompt to paste)
4. Check `DJANGO_ALLOWED_HOSTS` — if empty, prompt (required for prod)
5. Re-sync deps: `uv sync` (drops dev extras)
6. Print summary of what changed

Uses `dotenv_set` (overwrites existing values) and `dotenv_has_value` (checks non-empty).

Flags: `--help` for usage. Idempotent — running twice is harmless.

### Health check addition

In `run_all_checks()` in `bin/lib/health_check.sh`, inside the bare-metal branch, after `run_django_checks` and before the dev-only extras block:

```bash
if [ "$env" = "prod" ] && [ -f "$PROJECT_DIR/.env" ]; then
    local debug_val
    debug_val=$(grep -E "^DJANGO_DEBUG=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2-)
    if [ "$debug_val" = "1" ]; then
        hc_warn "debug_prod" "DEBUG is enabled in production (run: bin/set_production.sh)"
    fi
fi
```

## File Changes

| File | Change |
|------|--------|
| `bin/set_production.sh` | Create: production conversion script |
| `bin/lib/health_check.sh` | Add DEBUG-in-prod warning to `run_all_checks()` |
| `bin/tests/test_set_production.bats` | Syntax and --help tests |

## Non-Goals

- Reverse operation (prod → dev)
- Changing the installer flow
- Modifying deploy scripts