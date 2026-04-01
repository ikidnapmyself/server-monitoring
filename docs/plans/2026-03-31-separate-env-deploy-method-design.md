---
title: "Separate Environment from Deployment Method"
parent: Plans
nav_order: 79739668
---

# Separate Environment from Deployment Method — Design

**Date:** 2026-03-31
**Status:** Approved
**Tracks:** ikidnapmyself/server-monitoring#104

## Problem

`INSTALL_MODE` in `bin/install.sh` conflates two orthogonal concerns:

| INSTALL_MODE | Environment | Deployment |
|---|---|---|
| `dev` | Development | Bare-metal |
| `prod` | Production | Bare-metal (systemd) |
| `docker` | Unspecified | Docker Compose |

This makes it impossible to represent valid real-world combinations like
production-on-Docker (`DJANGO_ENV=prod` + `DEPLOY_METHOD=docker`) or
development-in-Docker for integration testing.

Additionally, `detect_mode()` in `bin/lib/health_check.sh` uses runtime
heuristics (containers running, systemd unit present) rather than explicit
configuration from `.env`, making mode detection fragile.

## Goal

Introduce two independent configuration axes:

- **`DJANGO_ENV`** — `dev` or `prod` (already in `.env.sample`)
- **`DEPLOY_METHOD`** — `bare` or `docker` (new)

Update `bin/install.sh`, `bin/lib/health_check.sh`, and the deploy scripts
to read and write both variables, enabling all four combinations:

| DJANGO_ENV | DEPLOY_METHOD | Meaning |
|---|---|---|
| `dev` | `bare` | Local development on bare-metal |
| `dev` | `docker` | Local development via Docker Compose |
| `prod` | `bare` | Production on bare-metal (systemd + gunicorn) |
| `prod` | `docker` | Production via Docker Compose |

## Design

### `.env.sample`

Add `DEPLOY_METHOD=bare` alongside the existing `DJANGO_ENV=dev`.

```
DJANGO_ENV=dev
DEPLOY_METHOD=bare
```

### `bin/install.sh`

Replace the single three-way mode prompt with two sequential prompts:

```
Select environment:
  1) dev   — development (DEBUG=1, eager tasks)
  2) prod  — production (DEBUG=0, real secret key, prod Celery)

Select deployment method:
  1) bare   — bare-metal (Python + uv, managed by systemd or runserver)
  2) docker — Docker Compose stack (requires Docker running)
```

`INSTALL_MODE` is removed. The two selections drive the rest of the installer:

- `dotenv_prompt_setup` is unified — it writes both `DJANGO_ENV` and
  `DEPLOY_METHOD` to `.env`, then prompts for env-specific and
  method-specific variables.
- `dotenv_prompt_docker` is removed (its logic merges into
  `dotenv_prompt_setup`).
- Docker daemon check moves to the deployment-method branch.
- Dependency installation, migrations, and post-install steps run only for
  `bare` deploy; for `docker` the script delegates to `deploy-docker.sh`
  (as before).

#### Prompt matrix

| Variable | dev+bare | dev+docker | prod+bare | prod+docker |
|---|---|---|---|---|
| `DJANGO_DEBUG` | Prompt (default 1) | Prompt (default 1) | Force 0 | Prompt (default 0) |
| `DJANGO_SECRET_KEY` | Optional generate | Prompt generate | Required generate | Required generate |
| `DJANGO_ALLOWED_HOSTS` | Default `localhost` | Default `localhost` | Required | Required |
| `CELERY_BROKER_URL` | Optional | Skip (Compose provides) | Required | Skip (Compose provides) |
| `CELERY_TASK_ALWAYS_EAGER` | Prompt | Force 0 | Force 0 | Force 0 |

### `bin/lib/health_check.sh`

#### New helper functions

**`detect_env()`** — returns `dev` or `prod`:

```
1. Read DJANGO_ENV from PROJECT_DIR/.env  → return value if present
2. Fallback → "dev"
```

**`detect_deploy_method()`** — returns `docker` or `bare`:

```
1. Read DEPLOY_METHOD from PROJECT_DIR/.env  → return value if present
2. Heuristic fallback (backward compat):
   a. Docker compose containers running → "docker"
3. Default → "bare"
   (systemd is a service manager within bare-metal, not a DEPLOY_METHOD value;
   bare-metal deployments — whether managed by systemd or not — all return "bare")
```

#### Updated `detect_mode()`

Keep `detect_mode()` for backward compatibility (used by `bin/update.sh` and
other callers). Reimplement it on top of the two new helpers:

```
method=$(detect_deploy_method)
env=$(detect_env)

if   method == docker  → return "docker"
elif method == bare && systemctl unit exists → return "systemd"
elif env == prod       → return "prod"
else                   → return "dev"
```

#### Updated `run_all_checks()`

Dispatch on both axes independently:

```
method=$(detect_deploy_method)
env=$(detect_env)

run_core_checks    # always (when method=bare)
run_django_checks  # always (when method=bare)
run_dev_checks     # when env=dev AND method=bare
run_docker_checks  # when method=docker
run_systemd_checks # when method=bare AND systemd unit exists
```

### `bin/deploy-docker.sh`

Add a `.env` validation step in the pre-flight section:

- Read `DEPLOY_METHOD` from `.env`
- If present and not `docker`: print a warning and ask the user to confirm
  (the existing Docker pre-flight still runs either way)
- If absent: write `DEPLOY_METHOD=docker` to `.env` and note it

### `bin/deploy-systemd.sh`

Same pattern:

- Read `DEPLOY_METHOD` from `.env`
- If present and not `bare`: print a warning
- If absent: write `DEPLOY_METHOD=bare` to `.env`

## File Changes

| File | Change |
|---|---|
| `.env.sample` | Add `DEPLOY_METHOD=bare` |
| `bin/install.sh` | Two prompts, remove `INSTALL_MODE`, merge `dotenv_prompt_docker` |
| `bin/lib/health_check.sh` | Add `detect_env()`, `detect_deploy_method()`, update `detect_mode()`, update `run_all_checks()` |
| `bin/deploy-docker.sh` | Validate `DEPLOY_METHOD` from `.env` |
| `bin/deploy-systemd.sh` | Validate `DEPLOY_METHOD` from `.env` |
| `bin/tests/lib/test_health_check.bats` | Tests for `detect_env()` and `detect_deploy_method()` |

## Non-Goals

- Changing systemd unit files (they already use `EnvironmentFile=`)
- Adding new Docker Compose profiles
- Changing `bin/update.sh` (it uses `detect_mode()` which is kept stable)
