---
title: "Unified Health Check"
parent: Plans
nav_order: 79739670
---

# Unified Health Check — Design

**Date:** 2026-03-29

## Problem

There are two separate "installation check" surfaces that overlap but cover different things:
- `check_installation()` in `bin/cli/install_menu.sh` — checks uv, .venv, pre-commit, aliases, Django
- `bin/check_system.sh` — checks uv, Python, .env, .venv, disk, writable, Django preflight

Neither covers Redis, Celery broker, database migrations, Docker container health, or systemd service status. They share no code.

## Goal

Unify into a single `bin/lib/health_check.sh` library that auto-detects the deployment mode and runs the relevant checks. Both `check_system.sh` and cli's `check_installation()` call the same library.

## Auto-detection

The library detects deployment mode by examining what's present:

```
Priority (first match wins):
1. Docker   — docker compose containers running for this project
2. systemd  — server-monitoring.service unit exists in systemd
3. prod     — .venv exists + DJANGO_ENV=prod in .env
4. dev      — fallback
```

## Check Groups

| Group | dev | prod | docker | systemd |
|-------|-----|------|--------|---------|
| Core (Python, uv, .env, disk, writable) | Yes | Yes | Skip | Skip |
| Django (check, migrations) | Yes | Yes | Skip | Skip |
| Dev (pre-commit, aliases) | Yes | No | No | No |
| Docker (daemon, compose, containers) | No | No | Yes | No |
| systemd (units, Redis, socket) | No | No | No | Yes |

## Check Inventory

**Core checks** (dev + prod):
- Python 3.10+ installed (with pyenv shim detection)
- uv installed
- `.env` file exists
- `.venv` directory exists
- Project directory writable
- Disk space > 1GB free

**Django checks** (dev + prod, requires .venv):
- `manage.py check` passes
- No pending migrations (`manage.py migrate --check`)

**Dev checks** (dev only):
- Pre-commit hooks installed
- Shell aliases configured

**Docker checks** (docker only):
- Docker daemon running
- `docker compose` v2 available
- All 3 containers running (redis, web, celery)
- No containers in restart loop

**systemd checks** (systemd only):
- `server-monitoring.service` is active
- `server-monitoring-celery.service` is active
- Redis service is active
- Gunicorn socket exists (`/run/server-monitoring/gunicorn.sock`)

## Output

- Default: human-readable `OK/WARN/ERR` with summary line. Exit code 1 if any errors.
- `--json`: array of `{"check": "name", "status": "ok|warn|err", "message": "..."}` objects.

## File Changes

**New:**
- `bin/lib/health_check.sh` — unified library with `detect_mode()`, check groups, `run_all_checks()`, JSON support
- `bin/tests/lib/test_health_check.bats` — unit tests for detect_mode and check functions

**Modified:**
- `bin/check_system.sh` — slim to flag parsing + call `run_all_checks`
- `bin/cli/install_menu.sh` — `check_installation()` calls `run_all_checks`
- `bin/tests/test_check_system.bats` — add `--json` flag test

**Unaffected:** install.sh, deploy scripts, other cli modules.

## Approach

Grouped functions (selected): `run_core_checks()`, `run_django_checks()`, `run_dev_checks()`, `run_docker_checks()`, `run_systemd_checks()`, with `run_all_checks()` as the orchestrator that auto-detects mode.

Rejected:
- Registry pattern — over-engineered for ~15-20 checks
- Django management command — can't check pre-Django prerequisites from Python