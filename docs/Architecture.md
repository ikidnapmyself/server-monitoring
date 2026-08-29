---
title: Architecture
layout: default
nav_order: 2
---

# Architecture

## Overview

Server-maintanence is a Django-based server monitoring and alerting system. It ingests alerts from external sources, runs health checks, generates AI-powered recommendations, and dispatches notifications — all coordinated through a strict 4-stage pipeline.

**Tech stack:** Django 5.2, psutil (system metrics), Jinja2 (notification templates). The pipeline is broker-free — durable ingest + a `process_inbox` drain (no Celery/Redis).

## Pipeline Stages

The core pipeline processes events through four sequential stages, each owned by a dedicated Django app:

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ INGEST  │───▶│  CHECK  │───▶│ ANALYZE │───▶│ NOTIFY  │
│ alerts  │    │checkers │    │  intel  │    │ notify  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
```

| Stage | App | What it does | Input | Output |
|-------|-----|-------------|-------|--------|
| **INGEST** | `apps.alerts` | Parse webhook payloads, create Alert + Incident records | Raw JSON payload | `IngestResult` (incident, alerts) |
| **CHECK** | `apps.checkers` | Run system health checks (CPU, memory, disk, network, process) | Incident context | `CheckResult` (status, metrics) |
| **ANALYZE** | `apps.intelligence` | Generate AI recommendations via provider pattern (local/OpenAI) | Incident + check results | `AnalyzeResult` (recommendations) |
| **NOTIFY** | `apps.notify` | Dispatch notifications via driver pattern (email, Slack, PagerDuty) | Analysis results | `NotifyResult` (delivery status) |

The **orchestration app** (`apps.orchestration`) controls all stage transitions. Stages never call downstream stages directly.

### Use Cases

Not every deployment uses all four stages. The pipeline is composable — pick the stages you need:

**Local server monitoring** — You want to monitor CPU, memory, and disk on this machine and get notified when something is wrong. No external monitoring tools required. Health checks run on a cron schedule, generate alerts locally, and dispatch notifications.

```
Checkers -> Notify                      (local-monitor)
Checkers -> Intelligence -> Notify      (local-smart, adds AI analysis)
```

**External alert processing** — You already use Grafana, AlertManager, PagerDuty, or other monitoring tools. This system receives their webhooks, optionally enriches them with local health checks and AI analysis, and forwards notifications to your preferred channels.

```
Alert -> Notify                                         (direct)
Alert -> Checkers -> Notify                             (health-checked)
Alert -> Intelligence -> Notify                         (ai-analyzed)
Alert -> Checkers -> Intelligence -> Notify             (full pipeline)
```

**Central alert hub** — This server acts as an aggregation point for multiple monitored servers. It receives webhooks from various sources, runs AI analysis, and dispatches notifications. No local health checks needed.

```
Alert -> Intelligence -> Notify         (ai-analyzed)
```

See the [Setup Guide](Setup-Guide) for step-by-step walkthroughs.

### Hub Self-Monitoring

**A hub is a node in its own registry.** Whenever a machine records checker results
locally, `CheckAlertBridge` upserts that machine into the `Node` table
(`last_source = "local"`; a push from another machine sets `"cluster"`). So the box
running the hub appears in the fleet it aggregates, and its own full disk becomes an
alert, an incident and a page exactly like any agent's.

Two delivery modes, and when each applies:

| Mode | Command | What happens | Use when |
|------|---------|--------------|----------|
| **Inline** | `check_health` | Runs the checkers and records `Alert`/`Incident` rows synchronously. Records by default; `--no-alert` prints only. **Enqueues nothing** | A single machine with no hub, no cron and nobody draining an inbox — you still want alerts and incidents |
| **Scheduled** | `run_pipeline --checks-only` | Enters the pipeline at CHECK instead of INGEST. That stage produces the subject alert, the lane is resolved from it, and the lane's stages run — same routing, same executors as webhook traffic. Synchronous, and it drains its own downstream runs | The default. This is what `bin/install/cron.sh` schedules on every machine |
| **Through the inbox** | `push_to_hub --local` | Runs the checkers and records the same `PENDING` `PipelineRun` a remote agent's POST would have recorded, for `process_inbox` to drain through `IngestExecutor` + `ClusterDriver` | You want the hub's own checks queued and retried like a peer's push. Needs a running `process_inbox`, so it is not scheduled by the installer |

`bin/install/cron.sh` schedules `run_pipeline --checks-only` on every machine, and adds a
**push to hub** job only where `HUB_URL` is set. A machine without a hub needs no second
job: the health check already routes and notifies on its own.

**One identity, both producers.** A checker-origin alert is fingerprinted
`check:{instance_id}:{checker_name}` under `source: cluster`, with the stable name
`"<CHECKER> Check Alert"`. `instance_id` (from `INSTANCE_ID`, hostname when unset) is
used rather than the hostname because hostnames collide across stock installs and change
on rename. Alert identity is the pair `(fingerprint, source)` and incident grouping
matches on the alert *name*, so both producers — the local bridge and the pushed payload —
must agree on all three or one condition on one machine splits into several rows and
several incidents.

**No lane of its own.** The hub's checker traffic routes through `cluster-nodes` like any
node's; the old record-only `hub-self-check` lane is retired (deactivated, not deleted, by
migration `0018`) so the hub can page about itself. See
[Deployment → Routing](Deployment.md).

### Stage Configuration

Stage behavior is controlled through routing pipelines and Django Admin — not environment variables:

- **Routing**: `PipelineDefinition` (Django Admin) matches the run's subject alert and its ordered `stages` list selects which downstream stages run; its single `channel` is the notify target. Unmatched traffic fails non-retryably as `no_route` — there is no implicit fallback.
- **Intelligence**: The `IntelligenceProvider` model (Django Admin) controls which AI provider is active.
- **Notify**: The `NotificationChannel` model (Django Admin) controls which channels are active via `is_active`.

## Entry Points

### Management Commands

| Command | App | Purpose |
|---------|-----|---------|
| `check_health [checkers...]` | checkers | Run health checks, display summary, **record alerts** for this machine. Flags: `--no-alert` (print only), `--list`, `--json`, `--fail-on-warning`, `--fail-on-critical` |
| `run_check <checker>` | checkers | Run a single checker with checker-specific options (`--samples`, `--per-cpu`, `--paths`, `--hosts`, `--names`) |
| `run_pipeline --checks-only` | orchestration | Run checks through pipeline. Additional flags: `--checkers`, `--no-incidents`, `--hostname`, `--label`, `--warning-threshold`, `--critical-threshold` |
| `get_recommendations` | intelligence | Get system recommendations. Flags: `--incident-id`, `--memory`, `--disk`, `--provider`, `--json`, `--list-providers` |
| `test_notify [driver]` | notify | Test notification delivery. Flags: per-driver config (`--webhook-url`, `--smtp-host`, etc.) |
| `run_pipeline` | orchestration | Run pipeline end-to-end. Flags: `--sample`, `--payload`, `--file`, `--dry-run`, `--checks-only` |
| `monitor_pipeline` | orchestration | View pipeline run history. Flags: `--limit`, `--status`, `--run-id` |
| `push_to_hub` | alerts | Run the checkers and push the results to a hub. Flags: `--local` (record a `PENDING` run here instead of POSTing), `--dry-run`, `--json`, `--checkers` |

### HTTP Endpoints

**Alerts** (`/alerts/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/alerts/webhook/` | Receive alert (auto-detect driver) |
| POST | `/alerts/webhook/<driver>/` | Receive alert (specific driver: alertmanager, grafana, pagerduty, datadog, newrelic, opsgenie, zabbix, generic) |

**Intelligence** (`/intelligence/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/intelligence/health/` | Health check |
| GET | `/intelligence/providers/` | List available AI providers |
| POST | `/intelligence/recommendations/` | Get recommendations for an incident |
| POST | `/intelligence/memory/` | Memory-specific analysis |
| POST | `/intelligence/disk/` | Disk-specific analysis |

**Notify** (`/notify/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/notify/send/` | Send notification (auto-detect driver) |
| POST | `/notify/send/<driver>/` | Send notification (specific driver) |
| POST | `/notify/batch/` | Batch send multiple notifications |
| GET | `/notify/drivers/` | List available drivers |
| GET | `/notify/drivers/<driver>/` | Driver detail and config requirements |

**Orchestration** (`/orchestration/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/orchestration/pipeline/` | Trigger pipeline (async — records a PENDING run for the drain) |
| POST | `/orchestration/pipeline/sync/` | Trigger pipeline (sync, waits for completion) |
| GET | `/orchestration/pipelines/` | List pipeline runs |
| GET | `/orchestration/pipeline/<run_id>/` | Get pipeline run status |
| POST | `/orchestration/pipeline/<run_id>/resume/` | Resume a failed pipeline |

### Durable ingest / drain

The pipeline is broker-free. The webhook (and the async trigger endpoint) record a
`PENDING` `PipelineRun`; `manage.py process_inbox` claims and executes it (supervised
`--loop` or cron). No Celery/Redis. See
[Deployment → Durable ingest & the inbox drain](Deployment.md).

### Django Admin

All apps register their models at `/admin/`:

| Admin Path | Models |
|------------|--------|
| `/admin/alerts/` | Alert, Incident, AlertHistory, Node |
| `/admin/checkers/` | CheckRun |
| `/admin/intelligence/` | AnalysisRun |
| `/admin/notify/` | NotificationChannel |
| `/admin/orchestration/` | PipelineRun, StageExecution, PipelineDefinition |

#### Network map

`/admin/map/` (staff-only) renders the routing table as a read-time projection —
no models, no stored state, just `PipelineDefinition` rows in the exact order
`resolve_pipeline` consults them. Each lane shows its match conditions, stage list,
and a state banner: **ok**, **shadowed** (an earlier lane provably matches everything
this one would, and is named), **inactive**, or **never matches** (malformed match).
Below that, a delivery line mirrors `delivery_gap()` 1:1: bound to a channel,
recording only (no `notify` stage), no channel (none configured, or the bound
channel is inactive), or channel driver not registered.

## Pipeline Execution

**Location:** `apps/orchestration/orchestrator.py`

Fixed 4-stage sequence: INGEST → CHECK → ANALYZE → NOTIFY, each with a dedicated
executor class. The pipeline's *shape* is data, not code: after the **entry stage**
(INGEST for webhook traffic, CHECK for `run_pipeline --checks-only`), the
orchestrator resolves the matching `PipelineDefinition` from the alert that stage
produced (`routing.py`, first-match-wins by `priority`, ties on `id`) and runs the
downstream stages listed in its `stages` column, in that order; NOTIFY sends to the
matched pipeline's single `channel`. A no-match is a non-retryable `no_route`
failure — migration `0012` seeds a `catch-all` lane so unmatched traffic is a row
an operator can read and edit rather than a constant in the orchestrator.

**One run per incident event, not per push.** A push run executes its entry stage and
stops. Every incident that push *materially changed* — created, severity moved, status
transitioned, or its per-checker context key changed — becomes its own `PENDING`
downstream run that resolves its own lane and runs it. A steady-state re-push that says
nothing new starts no run at all, which is also what keeps a five-minute cron from
re-notifying ~288 times a day. Downstream runs inherit the push's `trace_id` with their
own `run_id`, so one push still reads as one story in `manage.py trace`; they are
drained by `process_inbox` like any other inbox work. Operator transitions
(`IncidentManager.acknowledge`/`resolve`/`close`) are a second producer of the same runs
(`origin=manual`), so an acknowledgement or resolution is announced on the next drain,
and the headline reads the incident's live status. Migration `0016` seeds
`resolved-all-clear`, which notifies an all-clear without paying for an AI analysis of
something that has already recovered. See
`docs/plans/2026-08-19-incident-fanout-design.md`.

- **Endpoints:** `POST /orchestration/pipeline/` (async — records a `PENDING` run for
  the `process_inbox` drain) and `/pipeline/sync/` (runs inline).
- **CLI:** `python manage.py run_pipeline --sample` / `--checks-only` / `--dry-run`.
- **Resume:** failed pipelines resume from the last successful stage.

Routing pipelines are managed in **Django Admin** (`/admin/orchestration/pipelinedefinition/`)
or wired by the guided `setup_cluster`. The legacy node/edge graph engine was retired
in Phase D — `PipelineDefinition` is now purely a routing rule (match → ordered stages → one channel).

**Observability:** a "Journey" panel on the Alert/Incident admin, `manage.py trace
<alert|trace_id>`, and `manage.py report` (per-node incidents, per-pipeline routing
hits, inbox depth) — all read-only projections over the `trace_id` chain.

## Data Models

### Core Models

```
Alert ──────┐
AlertHistory│──▶ Incident ──▶ PipelineRun ──▶ StageExecution
            │                      │
CheckRun ◀──┘                      │
AnalysisRun ◀──────────────────────┘
NotificationChannel (standalone config)
PipelineDefinition (standalone config)
```

| Model | App | Purpose |
|-------|-----|---------|
| `Alert` | alerts | Normalized alert record (fingerprint, status, severity, labels, raw payload) |
| `Incident` | alerts | Groups related alerts, tracks lifecycle (open → ack → resolved → closed) |
| `AlertHistory` | alerts | Audit trail of alert state transitions |
| `Node` | alerts | Registry of every machine that reports on itself, keyed by `instance_id` — this hub included |
| `CheckRun` | checkers | Health check execution log (status, metrics, timing, trace_id) |
| `AnalysisRun` | intelligence | AI analysis execution log (provider, status, timing, recommendations) |
| `PipelineRun` | orchestration | Pipeline execution tracking (status, timing, correlation IDs) |
| `StageExecution` | orchestration | Per-stage execution within a pipeline (input/output snapshots) |
| `NotificationChannel` | notify | Persistent channel configuration (driver, config, enabled) |
| `PipelineDefinition` | orchestration | Routing rule: match -> ordered stages -> one notify channel |

### State Machine

Pipeline runs progress through:

```
PENDING → INGESTED → CHECKED → ANALYZED → NOTIFIED (success)
                                    └──→ FAILED (terminal)
                                    └──→ RETRYING → (resume from last stage)
```

### Correlation IDs

Every pipeline run carries:
- `trace_id` — Correlation ID for tracing across all stages, logs, and DB records
- `run_id` — Unique ID for the specific pipeline run

## Configuration

### Key Environment Variables

Environment variables configure **infrastructure only**. Application behavior (which checkers to run, intelligence provider, notification channels) is managed through Django Admin and pipeline definitions.

| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | Required in production |
| `DJANGO_DEBUG` | Debug mode | `0` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `*` |
| `INBOX_DEPTH_WARN` | Drain backlog warning threshold | `500` |
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | Retries before pipeline failure | `3` |
| `ORCHESTRATION_BACKOFF_FACTOR` | Exponential backoff multiplier | `2.0` |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | Continue pipeline when AI fails | `1` |
| `ORCHESTRATION_METRICS_BACKEND` | Metrics backend (`logging` or `statsd`) | `logging` |
| `STATSD_HOST` | StatsD server host | `localhost` |
| `STATSD_PORT` | StatsD server port | `8125` |
| `STATSD_PREFIX` | StatsD metric prefix | `pipeline` |

### Settings

Django settings live in `config/settings.py`. Copy `.env.sample` to `.env` for local development.
