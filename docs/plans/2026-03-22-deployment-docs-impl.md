---
title: "2026-03-22 Deployment Documentation Implementation"
parent: Plans
---

# Deployment Documentation Implementation Plan

**Goal:** Add production deployment config files and documentation so users can deploy with Docker Compose or systemd + Nginx.

**Architecture:** Config files in `deploy/` (Docker, systemd, Nginx), full guide in `docs/Deployment.md`, summary section added to `docs/Installation.md`. Gunicorn added as a production dependency.

**Tech Stack:** Docker, Docker Compose, systemd, Nginx, gunicorn, uv

---

### Task 1: Add gunicorn dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add gunicorn as an optional production dependency**

In `pyproject.toml`, add a `prod` optional dependency group:

```toml
[project.optional-dependencies]
prod = [
    "gunicorn>=22.0.0",
]
```

**Step 2: Sync dependencies**

Run: `uv sync --extra dev`
Expected: gunicorn installed successfully

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add gunicorn production dependency"
```

---

### Task 2: Create Dockerfile

**Files:**
- Create: `deploy/docker/Dockerfile`

**Step 1: Write the Dockerfile**

```dockerfile
# --- Builder stage ---
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies (no dev extras)
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Ensure virtualenv is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings

# Collect static files
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Run migrations and start gunicorn
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3}"]
```

**Step 2: Verify the Dockerfile builds**

Run: `docker build -f deploy/docker/Dockerfile -t server-monitoring .`
Expected: Build completes without errors

**Step 3: Commit**

```bash
git add deploy/docker/Dockerfile
git commit -m "feat: add multi-stage Dockerfile for production"
```

---

### Task 3: Create docker-compose.yml

**Files:**
- Create: `deploy/docker/docker-compose.yml`

**Step 1: Write docker-compose.yml**

```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

  web:
    build:
      context: ../..
      dockerfile: deploy/docker/Dockerfile
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-8000}:8000"
    env_file: ../../.env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - ENABLE_CELERY_ORCHESTRATION=1
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - db-data:/app/db
      - static-data:/app/staticfiles

  celery:
    build:
      context: ../..
      dockerfile: deploy/docker/Dockerfile
    restart: unless-stopped
    command: celery -A config worker -l info
    env_file: ../../.env
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - ENABLE_CELERY_ORCHESTRATION=1
    depends_on:
      redis:
        condition: service_healthy

volumes:
  redis-data:
  db-data:
  static-data:
```

**Step 2: Verify compose config is valid**

Run: `docker compose -f deploy/docker/docker-compose.yml config --quiet`
Expected: No errors

**Step 3: Commit**

```bash
git add deploy/docker/docker-compose.yml
git commit -m "feat: add docker-compose.yml with web, celery, and redis"
```

---

### Task 4: Create Nginx config

**Files:**
- Create: `deploy/docker/nginx.conf`

**Step 1: Write nginx.conf**

```nginx
upstream django {
    server web:8000;
}

server {
    listen 80;
    server_name _;

    client_max_body_size 10M;

    location /static/ {
        alias /app/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}

# Uncomment and configure for SSL termination with Let's Encrypt:
#
# server {
#     listen 443 ssl;
#     server_name your-domain.com;
#
#     ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
#
#     # ... same location blocks as above ...
# }
#
# server {
#     listen 80;
#     server_name your-domain.com;
#     return 301 https://$host$request_uri;
# }
```

**Step 2: Commit**

```bash
git add deploy/docker/nginx.conf
git commit -m "feat: add Nginx reverse proxy config"
```

---

### Task 5: Create systemd unit files

**Files:**
- Create: `deploy/systemd/server-monitoring.service`
- Create: `deploy/systemd/server-monitoring-celery.service`

**Step 1: Write the gunicorn service unit**

`deploy/systemd/server-monitoring.service`:

```ini
[Unit]
Description=Server Monitoring (gunicorn)
After=network.target redis.service
Requires=redis.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/server-monitoring
EnvironmentFile=/etc/server-monitoring/env
ExecStart=/opt/server-monitoring/.venv/bin/gunicorn config.wsgi:application \
    --bind unix:/run/server-monitoring/gunicorn.sock \
    --workers 3 \
    --timeout 120
Restart=on-failure
RestartSec=5
RuntimeDirectory=server-monitoring

[Install]
WantedBy=multi-user.target
```

**Step 2: Write the Celery worker service unit**

`deploy/systemd/server-monitoring-celery.service`:

```ini
[Unit]
Description=Server Monitoring Celery Worker
After=network.target redis.service
Requires=redis.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/server-monitoring
EnvironmentFile=/etc/server-monitoring/env
ExecStart=/opt/server-monitoring/.venv/bin/celery -A config worker \
    --loglevel=info \
    --concurrency=2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Step 3: Commit**

```bash
git add deploy/systemd/
git commit -m "feat: add systemd units for gunicorn and celery worker"
```

---

### Task 6: Write docs/Deployment.md

**Files:**
- Create: `docs/Deployment.md`

**Step 1: Write the full deployment guide**

The document must include Jekyll front matter:

```yaml
---
title: Deployment
layout: default
nav_order: 4
---
```

Sections (follow the design doc outline exactly):

1. **Prerequisites** — Python 3.10+, uv, Redis
2. **Environment variables** — table from design doc (DJANGO_SECRET_KEY, DJANGO_DEBUG, DJANGO_ALLOWED_HOSTS, CELERY_BROKER_URL, ENABLE_CELERY_ORCHESTRATION, API_KEY_AUTH_ENABLED, RATE_LIMIT_ENABLED, WEBHOOK_SECRET_<DRIVER>)
3. **Option 1: Docker Compose** — step-by-step: clone, configure .env, `docker compose -f deploy/docker/docker-compose.yml up -d`, verify with curl to health endpoint, view logs
4. **Option 2: Bare metal with systemd** — step-by-step: install Redis, clone to /opt/server-monitoring, create venv with uv, install deps, create env file at /etc/server-monitoring/env, run migrations, collect static, copy systemd units, enable and start, verify
5. **Nginx reverse proxy** — explain the provided config, Docker vs systemd differences (upstream web:8000 vs unix socket), SSL with certbot pointer
6. **Webhook ingestion** — how alerts arrive (POST /alerts/webhook/), sync vs async (ENABLE_CELERY_ORCHESTRATION), automatic fallback, signature verification
7. **Monitoring the deployment** — preflight, monitor_pipeline, check_health, celery inspect ping

Keep it practical — commands users can copy-paste. No fluff.

**Step 2: Verify Jekyll front matter renders**

Check that the file starts with valid YAML front matter and doesn't contain unescaped Jinja2/Liquid syntax.

**Step 3: Commit**

```bash
git add docs/Deployment.md
git commit -m "docs: add production deployment guide"
```

---

### Task 7: Update Installation.md with section 8

**Files:**
- Modify: `docs/Installation.md` (add after section 7, before end of file)

**Step 1: Add section 8**

Append before the end of the file:

```markdown
---

## 8) Production deployment

For production deployment with Celery workers, Redis, and Nginx, see the
[Deployment Guide](Deployment.md). It covers:

- **Docker Compose** — full stack with Django, Celery, and Redis (recommended for quick deploys)
- **Bare metal / VPS** — systemd units for gunicorn and Celery worker
- **Nginx reverse proxy** — static files, proxy headers, SSL termination
- **Webhook ingestion** — async pipeline processing with automatic fallback
```

**Step 2: Commit**

```bash
git add docs/Installation.md
git commit -m "docs: add production deployment link to Installation.md"
```

---

### Task 8: Add STATIC_ROOT setting for collectstatic

**Files:**
- Modify: `config/settings.py` (near line 143)

**Step 1: Check if STATIC_ROOT exists**

Read `config/settings.py` and look for `STATIC_ROOT`. If it's missing, add it below `STATIC_URL`:

```python
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
```

This is required for `collectstatic` to work in production (Nginx serves from this directory).

**Step 2: Run collectstatic to verify**

Run: `uv run python manage.py collectstatic --noinput`
Expected: Files collected to `staticfiles/`

**Step 3: Add staticfiles/ to .gitignore if not already present**

Check `.gitignore` for `staticfiles/`. If missing, add it.

**Step 4: Commit**

```bash
git add config/settings.py .gitignore
git commit -m "chore: add STATIC_ROOT for production static file serving"
```

---

### Task 9: Final verification

**Step 1: Verify all new files exist**

Run: `ls -la deploy/docker/ deploy/systemd/ docs/Deployment.md`
Expected: Dockerfile, docker-compose.yml, nginx.conf, two .service files, Deployment.md

**Step 2: Verify Docker build works**

Run: `docker build -f deploy/docker/Dockerfile -t server-monitoring .`
Expected: Build succeeds

**Step 3: Run tests to ensure no regressions**

Run: `uv run pytest -q`
Expected: All tests pass

**Step 4: Run linters**

Run: `uv run pre-commit run --all-files`
Expected: All checks pass

**Step 5: Commit any fixups**

If linters required changes, commit them.