# Architecture

## Overview

Server-maintanence is a Django-based server monitoring and alerting system. It ingests alerts from external sources, runs health checks, generates AI-powered recommendations, and dispatches notifications — all coordinated through a strict 4-stage pipeline.

**Tech stack:** Django 5.2, Celery (async tasks), Redis (broker), psutil (system metrics), Jinja2 (notification templates).

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

### Skip Controls

Any stage can be skipped via environment variables:

```bash
CHECKERS_SKIP_ALL=1            # Skip all health checks
CHECKERS_SKIP=cpu,memory       # Skip specific checkers
NOTIFY_SKIP_ALL=1              # Skip all notifications
```

## Entry Points

### Management Commands

| Command | App | Purpose |
|---------|-----|---------|
| `check_health [checkers...]` | checkers | Run health checks, display summary. Flags: `--list`, `--json`, `--fail-on-warning`, `--fail-on-critical` |
| `run_check <checker>` | checkers | Run a single checker with checker-specific options (`--samples`, `--per-cpu`, `--paths`, `--hosts`, `--names`) |
| `check_and_alert` | alerts | Run checks and create alerts from results. Flags: `--dry-run`, `--no-incidents`, `--include-skipped` |
| `get_recommendations` | intelligence | Get system recommendations. Flags: `--incident-id`, `--memory`, `--disk`, `--provider`, `--json`, `--list-providers` |
| `list_notify_drivers` | notify | List available notification drivers. Flag: `--verbose` |
| `test_notify [driver]` | notify | Test notification delivery. Flags: per-driver config (`--webhook-url`, `--smtp-host`, etc.) |
| `run_pipeline` | orchestration | Run pipeline end-to-end. Flags: `--sample`, `--payload`, `--dry-run`, `--definition`, `--checks-only` |
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
| POST | `/orchestration/pipeline/` | Trigger pipeline (async via Celery) |
| POST | `/orchestration/pipeline/sync/` | Trigger pipeline (sync, waits for completion) |
| GET | `/orchestration/pipelines/` | List pipeline runs |
| GET | `/orchestration/pipeline/<run_id>/` | Get pipeline run status |
| POST | `/orchestration/pipeline/<run_id>/resume/` | Resume a failed pipeline |
| GET | `/orchestration/definitions/` | List pipeline definitions |
| GET | `/orchestration/definitions/<name>/` | Get definition detail |
| POST | `/orchestration/definitions/<name>/validate/` | Validate a definition |
| POST | `/orchestration/definitions/<name>/execute/` | Execute a definition |

### Celery Tasks

**Alert processing chain** (`apps.alerts.tasks`):

```
orchestrate_event → alerts_ingest → run_diagnostics → analyze_incident → notify_channels
```

Each stage task (except `orchestrate_event`) has `max_retries=3`.

**Pipeline tasks** (`apps.orchestration.tasks`):

| Task | Purpose |
|------|---------|
| `run_pipeline_task` | Run pipeline asynchronously, return result |
| `resume_pipeline_task` | Resume a failed pipeline from last successful stage |
| `start_pipeline_task` | Queue pipeline for async execution, return immediately |

### Django Admin

All apps register their models at `/admin/`:

| Admin Path | Models |
|------------|--------|
| `/admin/alerts/` | Alert, Incident, AlertHistory |
| `/admin/checkers/` | CheckRun |
| `/admin/intelligence/` | AnalysisRun |
| `/admin/notify/` | NotificationChannel |
| `/admin/orchestration/` | PipelineRun, StageExecution, PipelineDefinition |

## Orchestration Systems

The project provides two pipeline execution systems:

### Hardcoded Pipeline

**Location:** `apps/orchestration/orchestrator.py`

Fixed 4-stage sequence: INGEST → CHECK → ANALYZE → NOTIFY. Each stage has a dedicated executor class.

- **Endpoints:** `POST /orchestration/pipeline/` (async) and `/pipeline/sync/` (sync)
- **Celery support:** Yes — async mode queues via Celery
- **Resume:** Yes — failed pipelines can be resumed from the last successful stage
- **Use when:** Standard alert processing, existing webhook integrations

### Definition-Based Pipeline

**Location:** `apps/orchestration/definition_orchestrator.py`

Dynamic stages configured via JSON stored in `PipelineDefinition` model. Supports any combination and ordering of node types.

- **Endpoints:** `POST /orchestration/definitions/<name>/execute/`
- **Celery support:** Not yet (sync only)
- **Resume:** Not yet

**Available node types:**

| Type | Handler | Purpose |
|------|---------|---------|
| `ingest` | IngestNodeHandler | Process incoming alerts, create incidents |
| `context` | ContextNodeHandler | Gather system metrics (CPU, memory, disk) |
| `intelligence` | IntelligenceNodeHandler | AI analysis (local or OpenAI) |
| `notify` | NotifyNodeHandler | Send notifications |
| `transform` | TransformNodeHandler | Transform data between nodes |

**Example definition (standalone health check):**

```json
{
  "version": "1.0",
  "nodes": [
    {"id": "metrics", "type": "context", "config": {"include": ["cpu", "memory", "disk"]}, "next": "analyze"},
    {"id": "analyze", "type": "intelligence", "config": {"provider": "local"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack"}}
  ]
}
```

Definitions are created via Django Admin at `/admin/orchestration/pipelinedefinition/`.

### Comparison

| Feature | Hardcoded | Definition-based |
|---------|-----------|------------------|
| Configuration | Python code | JSON in database |
| Stages | Fixed 4 stages | Any combination of nodes |
| Deployment | Code deploy required | Admin UI |
| Retry logic | Built-in per stage | Built-in per node |
| Celery support | Yes (async mode) | Not yet (sync only) |
| Resume failed | Yes | Not yet |

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
| `PipelineDefinition` | orchestration | JSON pipeline definition for definition-based orchestration |

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

| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | Required in production |
| `DJANGO_DEBUG` | Debug mode | `0` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `*` |
| `CELERY_BROKER_URL` | Redis broker URL | `redis://localhost:6379/0` |
| `CELERY_TASK_ALWAYS_EAGER` | Run tasks synchronously (dev) | `False` |
| `CHECKERS_SKIP_ALL` | Skip all health checks | `False` |
| `CHECKERS_SKIP` | Comma-separated checkers to skip | Empty |
| `NOTIFY_SKIP_ALL` | Skip all notifications | `False` |

### Settings

Django settings live in `config/settings.py`. Copy `.env.sample` to `.env` for local development.
