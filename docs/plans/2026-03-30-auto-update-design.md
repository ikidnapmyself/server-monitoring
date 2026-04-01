---
title: "Auto-Update Script Design"
parent: Plans
nav_order: 79739669
---

# Auto-Update Script Design

**Date:** 2026-03-30
**Status:** Approved

## Goal

Create `bin/update.sh` — a shell script that automatically pulls updates from `origin/main`, syncs dependencies, runs migrations, restarts services, and notifies on success or failure. Integrates with cron via `setup_cron.sh`.

## Context

The project has `bin/install.sh` for first-time setup and `bin/deploy-*.sh` for deployment, but no mechanism to apply updates. Nodes (agents and hubs) need a brain-dead simple way to stay up to date.

Related: ikidnapmyself/server-monitoring#103 tracks separating environment (dev/prod) from deployment method (bare/docker). This design uses the existing `detect_mode` function from `bin/lib/health_check.sh` for mode detection, which will naturally benefit from that refactor when it lands.

## Approach

**Standalone `bin/update.sh`** (Approach A). Sources `bin/lib/` helpers and `detect_mode` from `health_check.sh`. Runs on every cron cycle — `git fetch` is cheap, exits early if HEAD matches origin/main.

## Flow

```
git fetch origin main
  HEAD == origin/main? → exit 0 (up to date)
  HEAD != origin/main? → continue:
    1. Save current HEAD SHA (for rollback)
    2. git pull origin main
    3. Sync .env with .env.sample (warn or auto-append)
    4. uv sync (--all-extras --dev for dev, plain for prod/docker)
    5. uv run python manage.py migrate
    6. Restart services (mode-dependent)
    7. Log result + best-effort notify
```

### On failure

- With `--rollback` flag: `git reset --hard $saved_sha` → `uv sync` → `migrate` → restart → notify failure
- Without `--rollback`: stop at failing step, log error, best-effort notify failure

## Mode Detection & Restart Logic

Uses `detect_mode` from `bin/lib/health_check.sh`:

| Mode | Restart command |
|------|----------------|
| `dev` | No restart (runserver auto-reloads) |
| `prod` / `systemd` | `sudo systemctl restart server-monitoring server-monitoring-celery` |
| `docker` | `docker compose -f deploy/docker/docker-compose.yml up -d --build` |

## Dependency Sync by Mode

| Mode | Command |
|------|---------|
| `dev` | `uv sync --all-extras --dev` |
| `prod` / `systemd` | `uv sync` |
| `docker` | `docker compose ... build` (handled by restart) |

## Flags

- `--rollback` — enable automatic revert on failure
- `--auto-env` — auto-append new `.env.sample` keys to `.env` (default: warn only)
- `--dry-run` — show what would happen, don't apply
- `--json` — JSON output
- `--help` — usage info

## Logging

Writes to `update.log` in project root with timestamps. Same pattern as `cron.log`.

## Notifications

Best-effort via `uv run python manage.py test_notify`. Suppresses errors if Django is broken.

| Event | Title | Message |
|-------|-------|---------|
| Success | `Update Succeeded` | `Updated from {old_sha} to {new_sha} ({commit_count} commits)` |
| Failure | `Update Failed` | `Failed at step: {step_name}. Error: {error}. Rolled back: yes/no` |

## Cron Integration

`bin/setup_cron.sh` gets a new prompt:

```
Enable automatic updates? [y/N]:
```

If yes, adds a cron entry on the same schedule as health checks:

```cron
*/5 * * * * cd /path/to/project && bin/update.sh >> update.log 2>&1
```

The `git fetch` early-exit makes this near-zero cost when there's nothing new.

## Exit Codes

- `0` — up to date or update succeeded
- `1` — update failed (with or without rollback)

## Env File Sync

After `git pull`, compares `.env.sample` against `.env`:
- Keys in `.env.sample` but not in `.env` are detected
- Default behavior: log a warning listing missing keys and their sample values
- With `--auto-env`: auto-append missing keys to `.env` with sample values
- Cron entry uses `--auto-env` for fully unattended operation
- On rollback, added env keys are not reverted (defaults are harmless)

## Non-Goals

- Changing `bin/install.sh` (tracked in #103)
- Branch selection (always `origin/main`)
- Partial updates or cherry-picking