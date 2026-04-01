---
title: "2026-03-06 Env Cleanup Design"
parent: Plans
---

# Environment Variable Cleanup — Design

## Problem

The `.env.sample` file contains dead keys, redundant app-level config that's already handled by DB models and pipeline definitions, disorganized comments, and legacy env var fallbacks in code. This creates confusion about what's actually configurable and where the source of truth lives.

## Principle

**Environment variables are for infrastructure. Application behavior is definition-based.**

- Pipeline definitions control which checkers run, which intelligence provider to use, which notify drivers to dispatch.
- `NotificationChannel` model controls channel config and enable/disable.
- `IntelligenceProvider` model controls provider selection and credentials.
- Env vars only exist for deploy-time infrastructure: Django core, Celery broker, metrics endpoints.

## What Gets Removed

### From `.env.sample`

| Key | Reason |
|-----|--------|
| `CHECKERS_SKIP_ALL` | Pipeline definitions specify `checker_names` per pipeline |
| `CHECKERS_SKIP` | Same — definitions control which checkers run |
| `NOTIFY_SKIP_ALL` | Never implemented; `NotificationChannel.is_active` handles it |
| `NOTIFY_SKIP` | Same |
| `OPENAI_API_KEY` | `IntelligenceProvider` model stores credentials in DB |
| `OPENAI_MODEL` | Same |
| `OPENAI_MAX_TOKENS` | Same |
| `INTELLIGENCE_PROVIDER` | Written by setup wizard but never read by the app |
| `DJANGO_DB_ENGINE` | `settings.py` hardcodes SQLite; not read from env |
| `DJANGO_DB_NAME` | Same |
| `DJANGO_DB_USER` | Same |
| `DJANGO_DB_PASSWORD` | Same |
| `DJANGO_DB_HOST` | Same |
| `DJANGO_DB_PORT` | Same |
| `DJANGO_DB_OPTIONS` | Same |

### From `settings.py`

- `CHECKERS_SKIP_ALL` setting and env var parsing
- `CHECKERS_SKIP` setting and `_skip_checkers` parsing
- Remove only the checker skip settings — all other settings stay

### From `apps/intelligence/providers/openai.py`

- Remove `os.environ.get("OPENAI_API_KEY")` fallback
- Remove `os.environ.get("OPENAI_MODEL")` fallback
- Remove `os.environ.get("OPENAI_MAX_TOKENS")` fallback
- Provider must receive config from `IntelligenceProvider` model (passed via `__init__` kwargs)

### From `apps/checkers/`

- `is_checker_enabled()` — remove global skip list from settings; pipeline definitions control this
- Any code that reads `settings.CHECKERS_SKIP_ALL` or `settings.CHECKERS_SKIP`

## What Stays

### `.env.sample` (infrastructure only, organized)

```
# Django
DJANGO_SECRET_KEY=
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_ENV=dev

# Celery / Redis
CELERY_BROKER_URL=redis://localhost:6379/0
# CELERY_RESULT_BACKEND=redis://localhost:6379/0
CELERY_TASK_ALWAYS_EAGER=0

# Orchestration
ORCHESTRATION_MAX_RETRIES_PER_STAGE=3
ORCHESTRATION_BACKOFF_FACTOR=2.0
ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=1
ORCHESTRATION_METRICS_BACKEND=logging

# StatsD (only used when ORCHESTRATION_METRICS_BACKEND=statsd)
STATSD_HOST=localhost
STATSD_PORT=8125
STATSD_PREFIX=pipeline

# Django System Checks
# SILENCED_SYSTEM_CHECKS=checkers.W009,checkers.W010
```

### `settings.py`

All orchestration, StatsD, Celery, and Django core settings remain env-var driven.

## Impact

- `setup_instance` wizard: stop writing `INTELLIGENCE_PROVIDER`, `OPENAI_*` to `.env`; it already creates DB records
- `.env.backup`: not tracked, user's local file — no action needed
- `.env.test`: local test file — no action needed (user manages it)
- Tests referencing `settings.CHECKERS_SKIP*`: update to not rely on these settings
- Documentation: update `docs/Architecture.md` env var table to match