---
title: "2026-03-26 Docker Install Automation"
parent: Plans
nav_order: 79739673
---

# Docker Install Automation — Design

**Date:** 2026-03-26

## Problem

The Docker Compose deployment path is fully documented (Deployment.md) but entirely manual: the user clones, edits `.env`, runs `docker compose build`, `docker compose up -d`, and verifies health by hand. The existing `bin/install.sh` automates dev and prod (bare-metal) modes but has zero Docker awareness.

## Goal

Extend the installation tooling so that `./bin/install.sh` offers a `docker` mode that automates the full Docker Compose deployment — from `.env` setup through health-verified running containers. Docker Engine is a hard prerequisite (not installed by the script).

## Design

### Mode Selection (install.sh changes)

The existing mode prompt changes from `dev / prod` to `dev / prod / docker`:

```
Select installation mode:
  1) dev    — local development (DEBUG=1, eager tasks)
  2) prod   — bare-metal production (systemd, gunicorn)
  3) docker — Docker Compose stack (requires Docker running)
```

When `docker` is selected:

1. Hard-fail if Docker daemon isn't running. Print: "Docker is required. Install it from https://docs.docker.com/get-docker/ and ensure the daemon is running."
2. Prompt `DJANGO_DEBUG` (default `1`)
3. Prompt `DJANGO_SECRET_KEY` (offer auto-generate)
4. Prompt `DJANGO_ALLOWED_HOSTS` (default `localhost,127.0.0.1`)
5. Skip `CELERY_BROKER_URL` prompt (Compose overrides it to internal Redis)
6. Skip Python/uv checks, dependency installation, migrations (all happen inside containers)
7. Write `.env`, delegate to `bin/deploy-docker.sh`

No dev/prod sub-menu within docker mode — the user's answers to the security questions determine the posture (DEBUG=1 for local, DEBUG=0 + real ALLOWED_HOSTS for production).

### New Script: `bin/deploy-docker.sh`

Standalone script called from `install.sh` or run directly.

**1. Pre-flight checks:**
- `.env` exists — if not, print "Run `./bin/install.sh` first" and exit
- Docker daemon is running — hard fail with install link
- `docker compose` (v2) available — fail if only legacy `docker-compose` v1

**2. Build & start:**
- `docker compose -f deploy/docker/docker-compose.yml build`
- `docker compose -f deploy/docker/docker-compose.yml up -d`

**3. Health verification (timeout 60s, poll every 5s):**
- **redis:** `docker compose exec redis redis-cli ping` — expects PONG
- **web:** container running + not in restart loop
- **celery:** container running + not in restart loop
- Print pass/fail per service, exit 1 if any fail

**4. Summary output:**
- Print running service URLs
- Print useful commands: `docker compose logs -f`, `docker compose down`, `docker compose ps`

### Changes to Existing Files

- **`bin/install.sh`** — Add `docker` as mode 3. Reuse existing `.env` prompt functions (SECRET_KEY, DEBUG, ALLOWED_HOSTS). Skip Python/uv checks, dependency install, migrations, CELERY_BROKER_URL prompt. After writing `.env`, call `bin/deploy-docker.sh`.
- **`docs/Deployment.md`** — Add note under Docker Compose section that setup can be automated via `./bin/install.sh` (docker mode).
- **No other files change.** Dockerfile, docker-compose.yml, and systemd files stay as-is.

## Approach

**Approach B (selected):** Extract `bin/deploy-docker.sh` as a standalone script, called from `install.sh`. This keeps Docker logic isolated and `install.sh` manageable. The standalone script guards against missing `.env` with a pre-flight check.

Rejected alternatives:
- **Inline in install.sh** — would bloat an already 373-line script
- **Shared lib refactor** — over-engineered for the scope