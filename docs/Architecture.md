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

### Stage Configuration

Stage behavior is controlled through routing pipelines and Django Admin — not environment variables:

- **Routing**: `PipelineDefinition` (Django Admin) matches an incident and its `run_checkers`/`run_intelligence`/`run_notify` flags select which stages run; its `channels` are the notify targets.
- **Intelligence**: The `IntelligenceProvider` model (Django Admin) controls which AI provider is active.
- **Notify**: The `NotificationChannel` model (Django Admin) controls which channels are active via `is_active`.

## Entry Points

### Management Commands

| Command | App | Purpose |
|---------|-----|---------|
| `check_health [checkers...]` | checkers | Run health checks, display summary. Flags: `--list`, `--json`, `--fail-on-warning`, `--fail-on-critical` |
| `run_check <checker>` | checkers | Run a single checker with checker-specific options (`--samples`, `--per-cpu`, `--paths`, `--hosts`, `--names`) |
| `run_pipeline --checks-only` | orchestration | Run checks through pipeline. Additional flags: `--checkers`, `--no-incidents`, `--hostname`, `--label`, `--warning-threshold`, `--critical-threshold` |
| `get_recommendations` | intelligence | Get system recommendations. Flags: `--incident-id`, `--memory`, `--disk`, `--provider`, `--json`, `--list-providers` |
| `test_notify [driver]` | notify | Test notification delivery. Flags: per-driver config (`--webhook-url`, `--smtp-host`, etc.) |
| `run_pipeline` | orchestration | Run pipeline end-to-end. Flags: `--sample`, `--payload`, `--file`, `--dry-run`, `--checks-only` |
| `monitor_pipeline` | orchestration | View pipeline run history. Flags: `--limit`, `--status`, `--run-id` |

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
| `/admin/alerts/` | Alert, Incident, AlertHistory |
| `/admin/checkers/` | CheckRun |
| `/admin/intelligence/` | AnalysisRun |
| `/admin/notify/` | NotificationChannel |
| `/admin/orchestration/` | PipelineRun, StageExecution, PipelineDefinition |

## Pipeline Execution

**Location:** `apps/orchestration/orchestrator.py`

Fixed 4-stage sequence: INGEST → CHECK → ANALYZE → NOTIFY, each with a dedicated
executor class. The pipeline's *shape* is data, not code: after INGEST, the
orchestrator resolves the matching `PipelineDefinition` for the incident
(`routing.py`, first-match-wins by `priority`) and runs the stages its
`run_checkers` / `run_intelligence` / `run_notify` flags enable; NOTIFY sends to the
matched pipeline's channels. A no-match runs the full order.

- **Endpoints:** `POST /orchestration/pipeline/` (async — records a `PENDING` run for
  the `process_inbox` drain) and `/pipeline/sync/` (runs inline).
- **CLI:** `python manage.py run_pipeline --sample` / `--checks-only` / `--dry-run`.
- **Resume:** failed pipelines resume from the last successful stage.

Routing pipelines are managed in **Django Admin** (`/admin/orchestration/pipelinedefinition/`)
or wired by the guided `setup_cluster`. The legacy node/edge graph engine was retired
in Phase D — `PipelineDefinition` is now purely a routing rule (match → flags → channels).

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
| `CheckRun` | checkers | Health check execution log (status, metrics, timing, trace_id) |
| `AnalysisRun` | intelligence | AI analysis execution log (provider, status, timing, recommendations) |
| `PipelineRun` | orchestration | Pipeline execution tracking (status, timing, correlation IDs) |
| `StageExecution` | orchestration | Per-stage execution within a pipeline (input/output snapshots) |
| `NotificationChannel` | notify | Persistent channel configuration (driver, config, enabled) |
| `PipelineDefinition` | orchestration | Routing rule: match -> run_* flags -> notify channels |

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
