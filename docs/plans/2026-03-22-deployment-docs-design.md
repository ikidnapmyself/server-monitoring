---
title: "2026-03-22 Deployment Documentation Design"
parent: Plans
nav_order: 79739677
---

# Deployment Documentation Design

**Date:** 2026-03-22
**Status:** Approved

## Goal

Add production deployment documentation and config files so users can deploy the full stack (Django + Celery + Redis + Nginx) on bare metal (systemd) or Docker Compose.

## Decisions

- **Both** bare metal and Docker paths documented
- **Full stack** Docker Compose: Django (gunicorn), Celery worker, Redis
- **Nginx** reverse proxy config included with SSL termination notes
- Config files committed to `deploy/` — usable out of the box
- Brief summary in `Installation.md` linking to full `Deployment.md`

## File Structure

```
docs/Deployment.md                          # Full production guide
deploy/
  docker/
    Dockerfile                              # Multi-stage build (uv + Django)
    docker-compose.yml                      # web + celery + redis services
    nginx.conf                              # Reverse proxy to gunicorn
  systemd/
    server-monitoring.service               # Gunicorn unit
    server-monitoring-celery.service         # Celery worker unit
docs/Installation.md                        # Add section 8 linking to Deployment.md
```

## Deployment.md Outline

### Prerequisites
- Python 3.10+, uv, Redis

### Environment Variables

Production-critical env vars in a table:

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `DJANGO_SECRET_KEY` | — | Yes | Cryptographic signing |
| `DJANGO_DEBUG` | `1` | Yes (set `0`) | Disable debug mode |
| `DJANGO_ALLOWED_HOSTS` | — | Yes | Comma-separated hostnames |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | No | Redis broker URL |
| `ENABLE_CELERY_ORCHESTRATION` | `0` | No | Enable async pipeline |
| `API_KEY_AUTH_ENABLED` | `0` | No | Require API keys for endpoints |
| `RATE_LIMIT_ENABLED` | `0` | No | Enable rate limiting middleware |

### Option 1: Docker Compose

- `docker compose up -d`
- What each service does (web, celery, redis)
- Volume mounts (db, static files)
- Verifying: health check endpoints, `docker compose logs`

### Option 2: Bare Metal / VPS with systemd

- Install Redis
- Install dependencies with uv
- Run migrations, collect static
- Set up gunicorn (bind to unix socket)
- Install systemd units (copy from `deploy/systemd/`)
- `systemctl enable --now` both services
- Verify with `systemctl status` and health endpoints

### Nginx Reverse Proxy

- Proxy to gunicorn unix socket
- Static files served directly by Nginx
- SSL termination notes (Let's Encrypt / certbot pointer, not a full guide)

### Webhook Ingestion

- How alerts arrive (`POST /alerts/webhook/`)
- Sync vs async flow (`ENABLE_CELERY_ORCHESTRATION`)
- Automatic fallback when broker is unreachable
- Signature verification (`WEBHOOK_SECRET_<DRIVER>`)

### Monitoring the Deployment

- `manage.py preflight` — system checks
- `manage.py monitor_pipeline` — pipeline run history
- `manage.py check_health` — health checks
- Celery worker health: `celery -A config inspect ping`

## Docker Details

### Dockerfile

Multi-stage build:
1. **Builder stage**: install uv, copy `pyproject.toml` + `uv.lock`, install deps
2. **Runtime stage**: copy installed packages + app code, run gunicorn

Gunicorn config: bind `0.0.0.0:8000`, 2-4 workers (configurable via `WEB_CONCURRENCY`).

### docker-compose.yml

Three services:
- `web`: Django + gunicorn, depends on redis, exposes 8000
- `celery`: same image, runs `celery -A config worker -l info`, depends on redis
- `redis`: official redis:7-alpine

Shared `.env` file for configuration. Named volume for SQLite DB persistence.

### nginx.conf

- Upstream pointing to `web:8000` (Docker) or unix socket (systemd)
- `/static/` served from collected static files
- Proxy headers: `X-Forwarded-For`, `X-Forwarded-Proto`, `Host`
- SSL server block placeholder with certbot paths

## systemd Details

### server-monitoring.service

- `ExecStart`: gunicorn via uv, bind to unix socket
- `WorkingDirectory`: project root
- `EnvironmentFile`: `/etc/server-monitoring/env`
- `Restart=on-failure`

### server-monitoring-celery.service

- `ExecStart`: celery worker via uv
- Same `EnvironmentFile` and `WorkingDirectory`
- `Restart=on-failure`

## Installation.md Changes

Add section 8 after "Pipeline workflow with aliases":

```markdown
## 8) Production deployment

For production deployment with Celery workers, Redis, and Nginx, see the
[Deployment Guide](Deployment.md). It covers:

- Docker Compose (recommended for quick deploys)
- Bare metal / VPS with systemd
- Nginx reverse proxy with SSL
- Webhook ingestion and async pipeline processing
```

## Out of Scope

- Kubernetes / Helm charts
- Database migration to PostgreSQL (SQLite is current default)
- CI/CD pipeline configuration
- Log aggregation / APM setup
- Let's Encrypt full walkthrough (just a pointer)