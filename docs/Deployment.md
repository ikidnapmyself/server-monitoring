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
- **Nginx** (reverse proxy, optional but recommended)

The pipeline runs **broker-free** — no Redis or Celery. Durable ingest records each
alert and `manage.py process_inbox` drains it (see below).

---

## Environment Variables

Create `/etc/server-monitoring/env` (systemd) or `.env` (Docker) with these values:

| Variable | Default | Required | Purpose |
|----------|---------|----------|---------|
| `DJANGO_SECRET_KEY` | — | **Yes** | Cryptographic signing key |
| `DJANGO_DEBUG` | `1` | **Yes** (set `0`) | Disable debug mode in production |
| `DJANGO_ALLOWED_HOSTS` | — | **Yes** | Comma-separated hostnames (e.g. `monitoring.example.com`) |
| `INBOX_DEPTH_WARN` | `500` | No | doctor warns once the PENDING drain backlog exceeds this |
| `API_KEY_AUTH_ENABLED` | `1` | No | API key auth (enabled by default; set `0` to disable for dev) |
| `RATE_LIMIT_ENABLED` | `0` | No | Enable rate limiting middleware |
| `HUB_API_KEY` | — | Agent only | Bearer token an agent uses to authenticate `push_to_hub` to the hub |

Minimal production `.env`:

```bash
DJANGO_SECRET_KEY=your-random-secret-key-here
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=monitoring.example.com
```

Generate a secret key:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Option 1: Docker Compose

The fastest way to get a production stack running. Includes Django (gunicorn) and the broker-free inbox drain.

> **Quick start:** Run `./bin/install.sh` and select **docker** mode to automate the steps below (`.env` setup, build, start, and health verification).

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

This starts two services:

| Service | What it does |
|---------|-------------|
| `web` | Django app served by gunicorn on port 8000 |
| `inbox` | Drain that processes recorded pipeline runs (`process_inbox --loop`) |

### 1.3 Verify

```bash
# Check all services are running
docker compose -f deploy/docker/docker-compose.yml ps

# Check logs
docker compose -f deploy/docker/docker-compose.yml logs web
docker compose -f deploy/docker/docker-compose.yml logs inbox

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

### 2.1 Clone and install

```bash
sudo mkdir -p /opt/server-monitoring
sudo chown www-data:www-data /opt/server-monitoring
sudo -u www-data git clone git@github.com:ikidnapmyself/server-monitoring.git /opt/server-monitoring
cd /opt/server-monitoring

# Install uv and dependencies as www-data
sudo -u www-data sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u www-data uv sync --frozen --no-dev --extra prod
```

### 2.2 Configure environment

```bash
sudo mkdir -p /etc/server-monitoring
sudo tee /etc/server-monitoring/env << 'EOF'
DJANGO_SECRET_KEY=your-random-secret-key-here
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=monitoring.example.com
EOF
sudo chown root:www-data /etc/server-monitoring/env
sudo chmod 640 /etc/server-monitoring/env
```

### 2.3 Run migrations and collect static files

```bash
cd /opt/server-monitoring
set -a; source /etc/server-monitoring/env; set +a

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput
```

### 2.4 Install systemd units

```bash
sudo cp deploy/systemd/server-monitoring.service /etc/systemd/system/
sudo cp deploy/systemd/server-monitoring-inbox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-monitoring server-monitoring-inbox
```

> **`server-monitoring-inbox` is required, not optional.** The webhook only *records*
> alerts (durable ingest); this drain is what actually processes them. See
> [Durable ingest & the inbox drain](#durable-ingest--the-inbox-drain) below. The
> legacy `server-monitoring-celery` unit is no longer needed — the pipeline runs
> broker-free.

> **Automated:** Run `sudo ./bin/install.sh deploy` to automate steps 2.3-2.5 (migrations, static files, unit installation, and service startup with health verification). Or use `sudo ./bin/install.sh` in **prod** mode when selecting the systemd deployment option.
>
> **Security note:** Running the installer with `sudo` executes all shell code as root. Review the deploy module (`bin/install/deploy.sh`) before running and ensure the repository has not been tampered with. Prefer running only `install.sh deploy` with `sudo` rather than the full installer to minimize the root-privileged surface.

### 2.5 Verify

```bash
sudo systemctl status server-monitoring
sudo systemctl status server-monitoring-inbox

# Test via unix socket
curl --unix-socket /run/server-monitoring/gunicorn.sock http://localhost/alerts/webhook/
```

---

## Durable ingest & the inbox drain

The alert webhook does **not** process pipelines inline. It writes the payload's
alerts (a bounded, size-capped write), lets incidents form, then **durably records** one
`PENDING` pipeline run per materially changed incident and returns
`202 {status: accepted, trace_id, incidents}` immediately. A **drain** then processes
the queue at a controlled rate. The slow stages — checkers, AI analysis, delivery —
stay queued, so the web workers remain responsive and a flood grows a **bounded
database queue** instead of OOM-ing the node — and it needs **no Redis or Celery**.

> ⚠️ **A drain must be running.** With neither the systemd service nor a cron entry
> below, alerts are recorded but **never processed** — they pile up as `PENDING`
> runs. Check the backlog any time with `manage.py doctor` (`Inbox: N pending`);
> `doctor` also emits a warning once the backlog passes `INBOX_DEPTH_WARN` (default
> 500).

**Option A — supervised loop (recommended, near-real-time).** The
`server-monitoring-inbox` unit installed above runs:

```bash
manage.py process_inbox --loop --interval 5 --limit 100
```

It polls every few seconds, restarts on crash, and needs no broker.

**Option B — cron one-shot (no systemd).** Drain on a schedule instead:

```cron
*/1 * * * * cd /opt/server-monitoring && .venv/bin/python manage.py process_inbox --limit 100
```

**Manual "process now".** Force a specific recorded run through immediately:

```bash
uv run python manage.py process_inbox --id <run_id>
```

**Crash recovery.** A run claimed by a drain that dies mid-flight is reclaimed after
`--stale-minutes` (default 15) and retried.

**Trade-off.** Processing is now eventually-consistent: under load there is a short,
visible queue delay (bounded by the drain interval) rather than synchronous handling.

> **No-drain deployments (planned).** A future opt-in synchronous mode will let a
> gunicorn-only host process alerts inline without a drain (trading back the flood
> protection). Until then, run a drain.

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

### Durable ingest response

The webhook writes the alerts, queues the incident work and returns immediately — it
never runs the pipeline inline:

| Behavior | Response |
|----------|----------|
| Alerts written; one `PENDING` run queued per changed incident | `202 Accepted` with `{status: accepted, trace_id, incidents}` |
| Payload carried no alerts (misconfigured sender) | `202 Accepted` with `incidents: []`, logged as a warning |
| Payload unusable — no driver matched it, nothing written | `400 Bad Request` |
| Body larger than 1 MiB | `413 Payload Too Large` |

The [inbox drain](#durable-ingest--the-inbox-drain) then processes the queued runs. No
broker is involved, and no incident is lost if processing lags — it stays queued.

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

The `--json` output lists routing lanes under `definitions[]`, each with `name`,
`active`, `priority`, `channel` (the channel's name, or `null` when the lane
targets none) and `channel_routes` (whether that channel is active enough to
actually deliver). A lane wired to a deactivated channel reports its name with
`channel_routes: false` — reporting the name alone would claim a route that does
not exist.

### Health checks

```bash
uv run python manage.py check_health            # CPU, memory, disk, network, process
uv run python manage.py check_health --list
uv run python manage.py check_health --no-alert  # print only, record nothing
```

`check_health` **records alerts and incidents for this machine by default** (and
registers it in the `Node` registry). It is synchronous and enqueues nothing, so a
single machine with no hub, no cron and nobody draining the inbox still gets alerts.
Use `--no-alert` when you only want to look.

### Pipeline history

```bash
uv run python manage.py monitor_pipeline --limit 10
```

### Inbox drain health

```bash
uv run python manage.py doctor            # Shows "Inbox: N pending, M processing"
systemctl status server-monitoring-inbox  # Is the drain running?
```

For Docker:

```bash
docker compose -f deploy/docker/docker-compose.yml logs inbox
```

---

## Upgrading an existing install to row-based routing

Routing now lives entirely in `PipelineDefinition` rows: the three `run_*`
booleans became one ordered `stages` list, the `channels` M2M became a single
`channel` FK, and the orchestrator's implicit "run everything" fallback is gone.
Migrations `0010`–`0014` carry an existing install across.

### Migrate first, then restart — the order is not symmetric

```bash
uv run python manage.py migrate      # 1. rows first
sudo systemctl restart server-monitoring server-monitoring-inbox   # 2. code second
```

**Migrating before the restart is safe.** The old code's fallback is simply never
reached, because the seeded `catch-all` lane reproduces exactly the default order
that fallback used to hard-code.

**Deploying the code first is not.** Until the rows exist, anything no lane claims
fails **non-retryably** as `no_route`. Those runs are not in the inbox, nothing
auto-retries them, and each one needs a manual resume once routing is configured.

### Back up before migrating

Both destructive steps are reversible, but one of them cannot restore what it
discards:

- **`0010`** backfills `stages` from the three booleans, then drops the columns.
- **`0011`** keeps **one** channel per lane — the same one delivery already picked,
  the alphabetically first *active* one — and discards the rest when the join table
  is dropped. No behaviour changes, since the discarded channels were never
  consulted, but the rows are gone: `backwards()` restores only the survivor.
  `forwards()` logs a warning naming every lane and every channel it drops, so
  check the `migrate` output and re-create anything you still want.

### Re-check `severity` and `instance` conditions

Routing facts now come from **one** alert rather than a merge across the incident,
so two fields are computed differently: `severity` is the subject alert's own (no
longer the incident's maximum across alerts), and `instance` falls through
`instance_id` → `instance` → `hostname` (no longer `instance_id` alone).

This is a superset of the *value*, but **not** of the *match outcome*. For `is-not`
and `not-in`, a fact moving from `""` to populated flips a previously-matching lane
to not-matching. Audit any lane that conditions on either field:

```bash
uv run python manage.py shell -c "
from apps.orchestration.models import PipelineDefinition
for d in PipelineDefinition.objects.all():
    for c in (d.match or []):
        if c.get('field') in ('severity', 'instance'):
            print(d.name, c)"
```

### After the restart

```bash
uv run python manage.py preflight --json   # definitions[]: stages, channel, channel_routes
```

Confirm every lane you rely on still has the `stages` you expect and a
`channel_routes: true` where it is meant to deliver.

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

Guided hub setup does not leave the notification channel bare — it binds it to the
**catch-all `PipelineDefinition`** that actually wins routing (on a fresh install
that is the seeded `catch-all` lane; if no catch-all exists at all it creates
`default-catch-all`). Notification routing is pipeline-driven:

- Each active `PipelineDefinition` has a `match` (a list of `{field, op, value}`
  conditions; `field` is `source`, `severity`, `instance`, `origin`, or
  `label:<key>`; `op` is `is` / `is-not` / `in` / `not-in`) and a `priority`.
  `origin` is where the run started — `incoming_webhook`, `checker_generated`, or
  `manual`.
- Facts come from the run's **subject alert** — one alert, not a merge across the
  incident. `severity` is that alert's own severity, and `instance` falls through
  `instance_id` → `instance` → `hostname`.
- Pipelines are evaluated by ascending `priority` (ties broken by `id`) and the
  **first match wins**. An empty `match` matches everything, so the catch-all is
  the backstop. The matched pipeline is **stamped on the `Incident`**, and the
  notify stage sends to that pipeline's `channel`.
- The pipeline's `stages` column is **one ordered list** naming the downstream
  stages to run — a subset of `["check", "analyze", "notify"]`, always in that
  order. The entry stage is not listed: the lane is resolved *from* the alert the
  entry stage produced, so it has already run. For example, `["analyze", "notify"]`
  produces an AI-analysed notify without re-running checks; `["check", "analyze"]`
  records and analyses without notifying; `[]` records the alert and stops.
- To route specific traffic elsewhere (e.g. send `severity: critical` from a given
  node to a dedicated channel, or silence a noisy source), add a higher-priority
  pipeline in **Orchestration → Pipeline definitions** with the narrower `match`,
  its own channel, and the `stages` you want. Lower `priority` numbers win, so an
  exception rule sits *above* the general one. Convention: **below 100** for system
  lanes that must pre-empt operator rules, **100** (the default) for your own
  lanes, **1000** for a catch-all that should only fire when nothing else claimed
  the alert.

Lanes seeded out of the box:

| Lane | Priority | Match | Stages |
|------|----------|-------|--------|
| `cluster-nodes` | 50 | `source is cluster` | `analyze`, `notify` |
| `catch-all` | 1000 | *(empty)* | `check`, `analyze`, `notify` |

None of these are special-cased in code — they are ordinary rows, editable and
deletable like any lane you create.

Notes:

- **There is no implicit fallback.** An alert that no active lane matches fails
  **non-retryably** as `no_route`: nothing is checked, analysed or notified, and
  no retry can conjure a lane. The seeded `catch-all` row is what keeps a fresh
  install routing everything; deleting or deactivating it is a supported choice,
  and it means unmatched traffic fails loudly instead of silently taking a route
  nobody configured. `preflight` warns when no active pipeline definitions exist.
- A lane with an empty `match` is a catch-all, and lanes are evaluated by
  `(priority, id)`. **Several catch-alls at the same priority means the lowest
  `id` wins and the rest never route** — check the `stages` and `channel` columns
  on the Pipeline definitions changelist if a lane you expect never fires.
- **A lane targets exactly one channel.** Delivery has never fanned out; the
  `channel` FK simply makes the field match the behaviour. An **inactive** channel
  routes nowhere — the lane delivers nothing through it and notify falls back to
  payload-driven selection — so the changelist marks such a channel `(inactive)`.
- `run_pipeline --checks-only` is an **entry stage**, not an override: CHECK runs,
  and the lane is resolved from the alert CHECK produced, exactly as INGEST does.
  Adding `--no-incidents` makes it a silent diagnostic — the run stops at CHECKED,
  resolves no lane, and analyses and notifies nothing. Alerts are still recorded.
  Adding `--no-notify` instead keeps the lane and its ANALYZE — SSH in, look at the
  machine in real time, read the local provider's suggestions — and pages nobody.
- **The hub has no lane of its own — it is a node.** Checker-origin alerts carry
  `source: cluster`, so the hub's own checks match `cluster-nodes` and are analysed
  and notified exactly like any agent's: a hub can page about its own full disk.
  The old record-only `hub-self-check` lane (`origin is checker_generated`, empty
  `stages`) existed only to stop a five-minute cron re-reporting a still-firing
  alert ~288 times a day; the incident change gate closed that — a repeat that says
  nothing new enqueues no downstream run at all. Migration `0018` **deactivates**
  that row rather than deleting it (`Incident.pipeline` is `SET_NULL`, so a delete
  would blank which lane handled every incident it ever routed), and it is no longer
  seeded on a fresh install. To keep hub checks record-only, add a higher-priority
  lane matching `origin is checker_generated` with empty `stages`.
- Alerts created **within a pipeline run** carry the run's `trace_id` (the journey
  chain). Alerts ingested directly outside a run — the synchronous webhook fallback
  and the node ingest handler — currently have a blank `trace_id`. Cluster-ingested
  alerts also link to their originating **`Node`** (resolved from the `instance_id`
  label). `trace_id` and `node` are both searchable/visible in the `Alert` admin.

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
# Every 5 minutes (push.log gets a concise per-push summary, not the full payload)
*/5 * * * * cd /opt/server-monitoring && uv run python manage.py push_to_hub >> push.log 2>&1
```

Or run manually:

```bash
uv run python manage.py push_to_hub              # Push all checker results
uv run python manage.py push_to_hub --dry-run    # Preview without sending
uv run python manage.py push_to_hub --checkers cpu,memory  # Specific checkers
uv run python manage.py push_to_hub --local     # Hub self-monitoring: record on the local inbox
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

The registry holds every machine that reports on itself, **this one included**: each
accepted cluster push **upserts a `Node`** (by `instance_id`, tracking hostname and
last-seen) with `last_source: cluster`, and every local check run that records alerts
upserts this machine with `last_source: local`. So a hub appears in its own registry
alongside its agents. Browse them read-only in Django admin under **Alerts → Nodes**.

`manage.py doctor` is the single read-only diagnostic — it runs the preflight
checks and reports the node's derived role, whether it is **accepting pushes**
(derived from active API keys + `API_KEY_AUTH_ENABLED`, the real ingest gate),
and how many nodes it knows (its own row included, once it has run a local check):

```bash
uv run python manage.py doctor          # human-readable
uv run python manage.py doctor --json   # machine-readable
```

If `doctor` shows `Accepting pushes: False`, the hub has no active API key (or
auth is off) — mint one with `create_api_key`. `Known nodes` counts every row,
including this machine's own, so if an agent pushed and the count did not go **up**,
the push isn't being accepted (check auth / the key scope).

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
| Alerts arrive on hub but no notifications fire     | No routing pipeline / channel | Run `uv run python manage.py setup_cluster` on the hub (wires a catch-all pipeline + channel) |
| `push_to_hub --dry-run` shows 0 alerts             | No checkers returned results  | Run `uv run python manage.py check_health` to verify checkers work           |