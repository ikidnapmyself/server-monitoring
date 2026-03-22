---
title: Deployment
layout: default
nav_order: 4
---

# Deployment

Production deployment guide for Server Monitoring. Choose Docker Compose for quick deploys or bare metal with systemd for full control.

[toc]

---

## Prerequisites

- Python **3.10+**
- [`uv`](https://github.com/astral-sh/uv)
- **Redis** (message broker for Celery)
- **Nginx** (reverse proxy, optional but recommended)

---

## Environment Variables

Create `/etc/server-monitoring/env` (systemd) or `.env` (Docker) with these values:

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `DJANGO_SECRET_KEY` | — | **Yes** | Cryptographic signing key |
| `DJANGO_DEBUG` | `1` | **Yes** (set `0`) | Disable debug mode in production |
| `DJANGO_ALLOWED_HOSTS` | — | **Yes** | Comma-separated hostnames (e.g. `monitoring.example.com`) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | No | Redis broker URL |
| `ENABLE_CELERY_ORCHESTRATION` | `0` | No | Enable async pipeline via Celery |
| `API_KEY_AUTH_ENABLED` | `0` | No | Require API keys for endpoints |
| `RATE_LIMIT_ENABLED` | `0` | No | Enable rate limiting middleware |
| `WEBHOOK_SECRET_<DRIVER>` | — | No | Signature verification per driver (e.g. `WEBHOOK_SECRET_GRAFANA`) |

Minimal production `.env`:

```bash
DJANGO_SECRET_KEY=your-random-secret-key-here
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=monitoring.example.com
ENABLE_CELERY_ORCHESTRATION=1
```

Generate a secret key:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Option 1: Docker Compose

The fastest way to get a production stack running. Includes Django (gunicorn), Celery worker, and Redis.

### 1.1 Clone and configure

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
cp .env.sample .env
```

Edit `.env` with the production values from the table above.

### 1.2 Start the stack

```bash
docker compose -f deploy/docker/docker-compose.yml up -d
```

This starts three services:

| Service | What it does |
|---------|-------------|
| `redis` | Message broker for Celery |
| `web` | Django app served by gunicorn on port 8000 |
| `celery` | Celery worker processing pipeline tasks |

### 1.3 Verify

```bash
# Check all services are running
docker compose -f deploy/docker/docker-compose.yml ps

# Check logs
docker compose -f deploy/docker/docker-compose.yml logs web
docker compose -f deploy/docker/docker-compose.yml logs celery

# Test health endpoint
curl http://localhost:8000/alerts/webhook/
```

### 1.4 Run migrations manually (if needed)

Migrations run automatically on container start. To run them manually:

```bash
docker compose -f deploy/docker/docker-compose.yml exec web python manage.py migrate
```

### 1.5 Create an API key

```bash
docker compose -f deploy/docker/docker-compose.yml exec web python manage.py shell -c "
from config.models import APIKey
key = APIKey.objects.create(name='my-service')
print(f'API Key: {key._raw_key}')
print('Save this key — it cannot be retrieved again.')
"
```

---

## Option 2: Bare Metal / VPS with systemd

For full control on a Linux server.

### 2.1 Install Redis

```bash
# Ubuntu/Debian
sudo apt install redis-server
sudo systemctl enable --now redis

# Verify
redis-cli ping   # Should return PONG
```

### 2.2 Clone and install

```bash
sudo mkdir -p /opt/server-monitoring
sudo chown www-data:www-data /opt/server-monitoring
sudo -u www-data git clone git@github.com:ikidnapmyself/server-monitoring.git /opt/server-monitoring
cd /opt/server-monitoring

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies with gunicorn
uv sync --frozen --no-dev --extra prod
```

### 2.3 Configure environment

```bash
sudo mkdir -p /etc/server-monitoring
sudo tee /etc/server-monitoring/env << 'EOF'
DJANGO_SECRET_KEY=your-random-secret-key-here
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=monitoring.example.com
CELERY_BROKER_URL=redis://localhost:6379/0
ENABLE_CELERY_ORCHESTRATION=1
EOF
sudo chmod 600 /etc/server-monitoring/env
```

### 2.4 Run migrations and collect static files

```bash
cd /opt/server-monitoring
set -a; source /etc/server-monitoring/env; set +a

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput
```

### 2.5 Install systemd units

```bash
sudo cp deploy/systemd/server-monitoring.service /etc/systemd/system/
sudo cp deploy/systemd/server-monitoring-celery.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-monitoring server-monitoring-celery
```

### 2.6 Verify

```bash
sudo systemctl status server-monitoring
sudo systemctl status server-monitoring-celery

# Test via unix socket
curl --unix-socket /run/server-monitoring/gunicorn.sock http://localhost/alerts/webhook/
```

---

## Nginx Reverse Proxy

A sample config is provided at `deploy/docker/nginx.conf`. It works for both Docker and systemd deployments with minor adjustments.

### Docker setup

Nginx runs on the host (or as another container) and proxies to the `web` service:

```nginx
upstream django {
    server web:8000;        # Docker service name
}
```

### systemd setup

Change the upstream to use the gunicorn unix socket:

```nginx
upstream django {
    server unix:/run/server-monitoring/gunicorn.sock;
}
```

### Install on the host

```bash
sudo apt install nginx
sudo cp deploy/docker/nginx.conf /etc/nginx/sites-available/server-monitoring
sudo ln -s /etc/nginx/sites-available/server-monitoring /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### SSL with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d monitoring.example.com
```

Certbot will modify the Nginx config to add SSL. The commented SSL block in `deploy/docker/nginx.conf` shows the manual configuration if you prefer.

---

## Webhook Ingestion

External monitoring tools (Grafana, AlertManager, PagerDuty, etc.) send alerts via webhook:

```
POST /alerts/webhook/              # Auto-detect driver from payload
POST /alerts/webhook/<driver>/     # Driver-specific endpoint
```

### Sync vs Async

The behavior depends on `ENABLE_CELERY_ORCHESTRATION`:

| Setting | Behavior | Response |
|---------|----------|----------|
| `0` (default) | Pipeline runs synchronously in the request | `200 OK` with results |
| `1` | Pipeline queued to Celery worker | `202 Accepted` with pipeline ID |

### Automatic fallback

When `ENABLE_CELERY_ORCHESTRATION=1` but the Redis broker is unreachable, the webhook view automatically falls back to synchronous processing. No alerts are lost.

### Signature verification

Set `WEBHOOK_SECRET_<DRIVER>` environment variables to enable HMAC signature verification:

```bash
WEBHOOK_SECRET_GRAFANA=your-grafana-webhook-secret
WEBHOOK_SECRET_ALERTMANAGER=your-alertmanager-secret
```

Requests with invalid signatures receive `403 Forbidden`.

---

## Monitoring the Deployment

### System preflight

```bash
uv run python manage.py preflight          # All system checks, grouped
uv run python manage.py preflight --json   # JSON output for CI
```

### Health checks

```bash
uv run python manage.py check_health       # CPU, memory, disk, network, process
uv run python manage.py check_health --list
```

### Pipeline history

```bash
uv run python manage.py monitor_pipeline --limit 10
```

### Celery worker health

```bash
celery -A config inspect ping              # Check if workers are responding
celery -A config inspect active            # Show active tasks
```

For Docker:

```bash
docker compose -f deploy/docker/docker-compose.yml exec celery celery -A config inspect ping
```