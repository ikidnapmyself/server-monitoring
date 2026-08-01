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
| `ENABLE_CELERY_ORCHESTRATION` | `1` | No | Enable async pipeline via Celery |
| `API_KEY_AUTH_ENABLED` | `1` | No | API key auth (enabled by default; set `0` to disable for dev) |
| `RATE_LIMIT_ENABLED` | `0` | No | Enable rate limiting middleware |
| `HUB_API_KEY` | — | Agent only | Bearer token an agent uses to authenticate `push_to_hub` to the hub |

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

> **Quick start:** Run `./bin/install.sh` and select **docker** mode to automate the steps below (`.env` setup, build, start, and health verification).

### 1.1 Clone and configure

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
cp .env.sample .env
```

Edit `.env` with the production values from the table above. The Docker Compose file reads
config from `.env` and automatically overrides `CELERY_BROKER_URL` to use the internal
`redis` service hostname — you do **not** need to change that value in `.env` for Docker
deployments.

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
# On Debian/Ubuntu the service is usually named redis-server
# On RHEL/Fedora/Arch it's redis
sudo systemctl enable --now redis-server

# Verify
redis-cli ping   # Should return PONG
```

### 2.2 Clone and install

```bash
sudo mkdir -p /opt/server-monitoring
sudo chown www-data:www-data /opt/server-monitoring
sudo -u www-data git clone git@github.com:ikidnapmyself/server-monitoring.git /opt/server-monitoring
cd /opt/server-monitoring

# Install uv and dependencies as www-data
sudo -u www-data sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u www-data uv sync --frozen --no-dev --extra prod
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
sudo chown root:www-data /etc/server-monitoring/env
sudo chmod 640 /etc/server-monitoring/env
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

> **Automated:** Run `sudo ./bin/install.sh deploy` to automate steps 2.4-2.6 (migrations, static files, unit installation, and service startup with health verification). Or use `sudo ./bin/install.sh` in **prod** mode when selecting the systemd deployment option.
>
> **Security note:** Running the installer with `sudo` executes all shell code as root. Review the deploy module (`bin/install/deploy.sh`) before running and ensure the repository has not been tampered with. Prefer running only `install.sh deploy` with `sudo` rather than the full installer to minimize the root-privileged surface.

### 2.6 Verify

```bash
sudo systemctl status server-monitoring
sudo systemctl status server-monitoring-celery

# Test via unix socket
curl --unix-socket /run/server-monitoring/gunicorn.sock http://localhost/alerts/webhook/
```

---

## Nginx Reverse Proxy

A sample config is provided at `deploy/docker/nginx.conf`. Two values must be adjusted per deployment:

| Setting | Docker | systemd |
|---------|--------|---------|
| `upstream` | `server web:8000;` | `server unix:/run/server-monitoring/gunicorn.sock;` |
| `location /static/ alias` | `/app/staticfiles/` (shared volume) | `/opt/server-monitoring/staticfiles/` |

### Docker setup

Nginx runs on the host (or as another container) and proxies to the `web` service. If Nginx runs as a separate container, it needs access to the same staticfiles volume or network.

### systemd setup

Change both the upstream and the static files path:

```nginx
upstream django {
    server unix:/run/server-monitoring/gunicorn.sock;
}

location /static/ {
    alias /opt/server-monitoring/staticfiles/;
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

### Webhook authentication

All non-GET webhook requests are authenticated by the API-key middleware
(`API_KEY_AUTH_ENABLED=1`). Callers send `Authorization: Bearer <token>` (or
`X-API-Key`), resolved against the `APIKey` model. Mint tokens with
`manage.py create_api_key --name "<label>"`.

Requests with a missing or invalid key receive `401 Unauthorized`. There is no
per-driver HMAC scheme — one credential type gates every entrypoint.

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

---

## Multi-Instance (Cluster)

Deploy multiple instances across servers: **agents** monitor locally and push alerts to a **hub** that runs the full pipeline (intelligence + notifications).

### Architecture

```
Agent (server-1)  ──POST──┐
Agent (server-2)  ──POST──┤──▶  Hub  ──▶  intelligence ──▶ notify
Agent (server-3)  ──POST──┘     (receives cluster alerts)
```

All instances run the same codebase. Role is determined by environment variables.

### Guided setup (recommended)

One command wires a node and **proves it works** — use this instead of editing
`.env` by hand:

```bash
# On the hub: enable auth, mint an agent key (shown once), wire a notification
# channel (so the hub actually notifies), and confirm it's accepting.
uv run python manage.py setup_cluster --role hub --name "web-03" \
    --notify-driver slack --notify-webhook https://hooks.slack.com/services/XXX
# Omit the --notify-* flags to be prompted; --no-notify to skip (the hub then
# receives pushes but sends nothing until you add a channel in admin).

# On the agent: write HUB_URL/INSTANCE_ID/HUB_API_KEY and verify with a live push.
uv run python manage.py setup_cluster --role agent \
    --hub-url https://monitoring-hub.example.com --instance-id web-03 --hub-api-key <token>
```

Run with no flags for an interactive prompt, or from the CLI: **`bin/cli.sh cluster`
→ "Set up this node as a hub/agent (guided)"**. The agent step names the failure
if the push is rejected — `401` (bad key), `403` (WAF blocks the agent User-Agent,
or the key's scope excludes `/alerts/webhook/cluster/`), or connection errors —
each with the fix. Add `--no-verify` to skip the live push.

The sections below describe the equivalent manual `.env` setup.

### How the hub routes an incident to a channel

Guided hub setup does not leave the notification channel bare — it binds it to a
**catch-all `PipelineDefinition`** (name `default-catch-all`, empty `match`, low
`priority`). Notification routing is pipeline-driven:

- Each active `PipelineDefinition` has a `match` (a list of `{field, op, value}`
  conditions; `field` is `source`, `severity`, `instance`, or `label:<key>`;
  `op` is `is` / `is-not` / `in` / `not-in`) and a `priority`.
- For an incident, pipelines are evaluated by ascending `priority` and the **first
  match wins**. An empty `match` matches everything, so the catch-all is the
  backstop. The matched pipeline is **stamped on the `Incident`** right after
  ingest, and the notify stage sends to that pipeline's **primary active channel**
  (the first active channel by name).
- The pipeline's `run_checkers` / `run_intelligence` / `run_notify` flags **select
  which stages run** after ingest. For example, a pipeline with `run_checkers=False`
  produces an AI-analysed notify without re-running checks; clearing `run_notify`
  records the incident without notifying. A pipeline with all three cleared just
  records the alert (ingest only).
- To route specific traffic elsewhere (e.g. send `severity: critical` from a given
  node to a dedicated channel, or silence a noisy source), add a higher-priority
  pipeline in **Orchestration → Pipeline definitions** with the narrower `match`,
  its own channel, and the flags you want. Lower `priority` numbers win, so an
  exception rule sits *above* the general one.

Notes:

- **No-match is non-breaking:** if no active pipeline matches, the full pipeline
  (checks → intelligence → notify) runs, exactly as before routing existed.
- The CLI overrides `--checks-only` / `skip_checkers` still take precedence over a
  pipeline's flags.
- Only one channel per pipeline is used today; multi-channel fan-out is future work.
- Every alert now carries the run's `trace_id`, and cluster-ingested alerts link to
  their originating **`Node`** (resolved from the `instance_id` label) — both are
  searchable/visible in the `Alert` admin.

### Agent setup

On each server you want to monitor:

1. Install the project (`./bin/install.sh` — select "agent" when prompted for cluster role)
2. Add to `.env`:

```bash
HUB_URL=https://monitoring-hub.example.com
INSTANCE_ID=web-server-01
HUB_API_KEY=<token created on the hub via create_api_key>
```

3. Schedule the push command via cron:

```bash
# Every 5 minutes
*/5 * * * * cd /opt/server-monitoring && uv run python manage.py push_to_hub --json >> push.log 2>&1
```

Or run manually:

```bash
uv run python manage.py push_to_hub              # Push all checker results
uv run python manage.py push_to_hub --dry-run    # Preview without sending
uv run python manage.py push_to_hub --checkers cpu,memory  # Specific checkers
```

> **Tip:** The installer and `bin/install.sh cron` can configure all of the above interactively. Manual `.env` editing is only needed if you skipped the prompts.

### Hub setup

On the central monitoring server:

1. Install the project (`./bin/install.sh` — select "hub" when prompted for cluster role)
2. Add to `.env`:

```bash
API_KEY_AUTH_ENABLED=1
```

Then mint one API key per agent and paste each token into that agent's `HUB_API_KEY` — an active key is what makes this node a receiver (hub):

```bash
uv run python manage.py create_api_key --name "web-server-01"
# The raw token is shown once — copy it immediately.
```

The hub accepts cluster payloads at `POST /alerts/webhook/cluster/` (authenticated by the API-key middleware) and processes them through the full pipeline. Each alert carries `instance_id` and `hostname` labels for per-server filtering.

### Standalone (default)

Existing installs with no `HUB_URL` and no active API key continue to work as standalone instances with no changes.

### Verification

After setting up an agent or hub, verify the configuration:

**Agent verification:**

```bash
# Dry-run: builds payload, shows what would be sent (no network call)
uv run python manage.py push_to_hub --dry-run

# Single push: sends one payload to the hub and reports the result
uv run python manage.py push_to_hub

# Push specific checkers only
uv run python manage.py push_to_hub --checkers cpu,memory --dry-run
```

**Hub verification:**

```bash
# Confirm the cluster driver is registered
uv run python manage.py shell -c "from apps.alerts.drivers import DRIVER_REGISTRY; print('cluster' in DRIVER_REGISTRY)"
# Expected output: True

# Check Django system checks pass
uv run python manage.py check
```

### Node registry & `doctor`

A hub keeps a first-class record of every agent that pushes to it: each accepted
cluster push **upserts a `Node`** (by `instance_id`, tracking hostname and
last-seen). Browse them read-only in Django admin under **Alerts → Nodes**.

`manage.py doctor` is the single read-only diagnostic — it runs the preflight
checks and reports the node's derived role, whether it is **accepting pushes**
(derived from active API keys + `API_KEY_AUTH_ENABLED`, the real ingest gate),
and how many agent nodes it knows:

```bash
uv run python manage.py doctor          # human-readable
uv run python manage.py doctor --json   # machine-readable
```

If `doctor` shows `Accepting pushes: False`, the hub has no active API key (or
auth is off) — mint one with `create_api_key`. If an agent pushed but `Known
nodes` stays 0, the push isn't being accepted (check auth / the key scope).

### Security

- **Always use HTTPS** for `HUB_URL` in production. Payloads contain server metrics and alert details.
- **`HUB_API_KEY`** is a Bearer token minted on the hub (`create_api_key`) and set on each agent. The agent sends it as `Authorization: Bearer <HUB_API_KEY>`; the hub verifies it with the API-key middleware. Keep `API_KEY_AUTH_ENABLED=1` on the hub.
- Each agent can have its own key; revoke a compromised agent by deactivating its `APIKey` on the hub without touching the others.
- The token is sent only over the (HTTPS) transport header, never inside the payload body.

### Cluster auth migration (from the shared HMAC secret)

Earlier builds authenticated agent pushes with a shared `WEBHOOK_SECRET_CLUSTER`
HMAC and a dead `CLUSTER_ROLE` knob. Both are removed; agents now use
`HUB_API_KEY`. Cutover (coordinated across nodes):

1. On the hub, ensure `API_KEY_AUTH_ENABLED=1`, then mint one key per agent:
   `uv run python manage.py create_api_key --name "<agent>"`.
2. On each agent, set `HUB_API_KEY=<token>` and remove `WEBHOOK_SECRET_CLUSTER`
   and `CLUSTER_ROLE` from `.env`.
3. Verify with `uv run python manage.py push_to_hub --dry-run`, then a live push.

### Troubleshooting

| Symptom                                          | Cause                        | Fix                                                                          |
|--------------------------------------------------|------------------------------|------------------------------------------------------------------------------|
| `push_to_hub` exits with "HUB_URL not configured" | `HUB_URL` missing from `.env` | Add `HUB_URL=https://your-hub.example.com` to `.env`                        |
| `push_to_hub` exits with connection refused       | Hub not running or wrong URL  | Verify hub is accessible: `curl -s $HUB_URL/alerts/webhook/cluster/`        |
| `push_to_hub` returns 401 Unauthorized            | Missing/invalid `HUB_API_KEY` | Mint a key on the hub (`create_api_key`) and set it as `HUB_API_KEY` on the agent |
| `push_to_hub` exits "HUB_API_KEY is not configured" | Agent has no key set        | Set `HUB_API_KEY` in the agent `.env`                                        |
| `push_to_hub` returns 403 but the **same request via `curl` succeeds** | A WAF/proxy in front of the hub blocks the agent's `User-Agent` | The agent sends `User-Agent: server-monitoring-agent/<ver>`; allowlist it (or the agent IP) at the WAF. Confirm with `curl -H "User-Agent: Python-urllib/3.11" ...` reproducing the 403 |
| `push_to_hub` returns 403 with body `API key not authorized for this endpoint` | The `APIKey.allowed_endpoints` allowlist excludes `/alerts/webhook/cluster/` | Clear the key's scope (empty = all) or add `/alerts/`; keys minted by `create_api_key` are unscoped by default |
| Alerts arrive on hub but no notifications fire     | Pipeline not configured       | Run `uv run python manage.py setup_instance` on the hub                      |
| `push_to_hub --dry-run` shows 0 alerts             | No checkers returned results  | Run `uv run python manage.py check_health` to verify checkers work           |