# Architecture Documentation Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a centralized Architecture doc, merge orchestration-pipelines.md into it, clean up app READMEs to remove duplication, and trim the root README to overview + links.

**Architecture:** Create `docs/Architecture.md` as the single source of truth for system architecture and entry points. App READMEs become app-specific deep dives. Root README becomes a lightweight hub.

**Tech Stack:** Markdown documentation only — no code changes.

---

## Task 1: Create `docs/Architecture.md`

**Files:**
- Create: `docs/Architecture.md`

**Step 1: Write the Architecture doc**

Create `docs/Architecture.md` with the following content. This is the complete file — copy it verbatim.

````markdown
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
````

**Step 2: Verify the file renders correctly**

```bash
# Check file exists and has content
wc -l docs/Architecture.md
```

Expected: ~200 lines.

**Step 3: Commit**

```bash
git add docs/Architecture.md
git commit -m "docs: add centralized Architecture doc with all entry points"
```

---

## Task 2: Delete `docs/orchestration-pipelines.md`

**Files:**
- Delete: `docs/orchestration-pipelines.md`

All content from this file has been merged into `docs/Architecture.md` (Orchestration Systems section).

**Step 1: Delete the file**

```bash
git rm docs/orchestration-pipelines.md
```

**Step 2: Commit**

```bash
git commit -m "docs: remove orchestration-pipelines.md (merged into Architecture.md)"
```

---

## Task 3: Update root `README.md`

**Files:**
- Modify: `README.md`

Remove the detailed pipeline usage sections (lines 46-224 — "Usage modes" through the end of the standalone monitor section). These are now covered by `docs/Architecture.md`. Replace with a short summary linking to Architecture.md.

**Step 1: Edit README.md**

Replace the "Usage modes" section (everything between the `## Install` section and `## Environment configuration`) with:

```markdown
## Usage modes

This project supports two modes — see [Architecture](docs/Architecture.md) for full details:

1. **Pipeline controller**: Ingest alerts and route through intelligence + notify stages.
2. **Individual server monitor**: Run health checks locally and optionally generate alerts.

Quick examples:

```bash
# Pipeline mode (sync, with sample alert)
uv run python manage.py run_pipeline --sample

# Standalone health checks
uv run python manage.py check_health

# Run checks and generate alerts
uv run python manage.py check_and_alert
```
```

Also update the documentation map to include Architecture.md:

```markdown
## Documentation map

- Architecture: [`docs/Architecture.md`](docs/Architecture.md)
- Installation: [`docs/Installation.md`](docs/Installation.md)
- Security: [`docs/Security.md`](docs/Security.md)
- Health checks (checkers): [`apps/checkers/README.md`](apps/checkers/README.md)
- Alert ingestion: [`apps/alerts/README.md`](apps/alerts/README.md)
- Notifications: [`apps/notify/README.md`](apps/notify/README.md)
- Intelligence/recommendations: [`apps/intelligence/README.md`](apps/intelligence/README.md)
- Pipeline orchestration: [`apps/orchestration/README.md`](apps/orchestration/README.md)
- Shell scripts & CLI: [`bin/README.md`](bin/README.md)
- Working with repo AI agents / conventions: [`agents.md`](agents.md)
```

Remove the Templates link (it's a stub) and add Architecture as the first entry.

**Step 2: Verify links**

```bash
# Check all linked files exist
ls docs/Architecture.md docs/Installation.md docs/Security.md apps/*/README.md agents.md bin/README.md
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: trim root README to overview + links to Architecture.md"
```

---

## Task 4: Clean up `apps/alerts/README.md`

**Files:**
- Modify: `apps/alerts/README.md`

**What to remove:** The "Note" link to root README development section (line 7) — it adds no value. No pipeline duplication to remove; alerts README is already app-focused.

**What to verify stays:** Webhook endpoints, 8 drivers, data model, Django Admin, services, creating alerts from checks.

**Step 1: Replace line 7**

Replace:
```markdown
> **Note:** For development setup (formatting, linting, testing), see the main [README](../../README.md#development).
```

With:
```markdown
> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline.
```

**Step 2: Commit**

```bash
git add apps/alerts/README.md
git commit -m "docs: update alerts README cross-reference to Architecture.md"
```

---

## Task 5: Clean up `apps/checkers/README.md`

**Files:**
- Modify: `apps/checkers/README.md`

**What to remove:**
- Lines 5-6: "Note" link to root README
- Lines 7-20: "Orchestration Integration" section describing the pipeline and two modes — this is now in Architecture.md

**What to replace with:**

```markdown
> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (CHECK stage).
```

Keep everything from "## What's included" onward — that's all app-specific.

**Step 1: Edit the file**

Replace lines 5-20 (from the Note through "Pipeline mode" section) with the single cross-reference line above.

**Step 2: Commit**

```bash
git add apps/checkers/README.md
git commit -m "docs: remove pipeline duplication from checkers README"
```

---

## Task 6: Clean up `apps/intelligence/README.md`

**Files:**
- Modify: `apps/intelligence/README.md`

**What to remove:**
- Lines 5-6: "Note" link to root README
- Lines 7-42: "Orchestration Integration" section (pipeline description, stage execution details, viewing analysis history) — all now in Architecture.md

**What to replace with:**

```markdown
> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (ANALYZE stage).
```

Keep everything from "## Features" onward.

**Step 1: Edit the file**

Replace lines 5-42 with the single cross-reference line.

**Step 2: Commit**

```bash
git add apps/intelligence/README.md
git commit -m "docs: remove pipeline duplication from intelligence README"
```

---

## Task 7: Clean up `apps/notify/README.md`

**Files:**
- Modify: `apps/notify/README.md`

**What to remove:**
- Lines 6-9: Two "Note" paragraphs (orchestration tracking note + dev setup link)

**What to replace with:**

```markdown
> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (NOTIFY stage).
```

Keep everything from "## What's included" onward.

**Step 1: Edit the file**

Replace lines 6-9 with the single cross-reference line.

**Step 2: Commit**

```bash
git add apps/notify/README.md
git commit -m "docs: update notify README cross-reference to Architecture.md"
```

---

## Task 8: Clean up `apps/orchestration/README.md`

**Files:**
- Modify: `apps/orchestration/README.md`

**What to remove:**
- Lines 1-7: Pipeline diagram and intro that duplicates Architecture.md

**What to replace with:**

```markdown
# Orchestration App

> See [Architecture](../../docs/Architecture.md) for the full pipeline overview, entry points, and orchestration system comparison.

This app controls the lifecycle of pipeline runs through a strict state machine.
```

Keep everything from "## Key Concepts" onward — state machine, correlation IDs, stage contracts, monitoring signals, API endpoints, configuration, management commands, models.

**Step 1: Edit the file**

Replace lines 1-7 with the replacement above.

**Step 2: Commit**

```bash
git add apps/orchestration/README.md
git commit -m "docs: remove pipeline duplication from orchestration README"
```

---

## Task 9: Update `CLAUDE.md` references

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update the "Key Documentation" section**

In `CLAUDE.md`, find the "Key Documentation" section and update it:

Replace:
```markdown
## Key Documentation

- `agents.md` — AI agent roles and pipeline contracts (read this for any significant work)
- `apps/<app>/README.md` — App-specific documentation
- `apps/<app>/agents.md` — App-specific AI guidance
- `docs/orchestration-pipelines.md` — Pipeline architecture details
```

With:
```markdown
## Key Documentation

- `docs/Architecture.md` — System architecture, all entry points, pipeline stages, data models
- `agents.md` — AI agent roles and pipeline contracts (read this for any significant work)
- `apps/<app>/README.md` — App-specific documentation
- `apps/<app>/agents.md` — App-specific AI guidance
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md references to Architecture.md"
```

---

## Task 10: Full verification

**Step 1: Check all cross-references resolve**

```bash
# All linked files exist
ls docs/Architecture.md docs/Installation.md docs/Security.md
ls apps/alerts/README.md apps/checkers/README.md apps/intelligence/README.md apps/notify/README.md apps/orchestration/README.md
ls agents.md bin/README.md
```

**Step 2: Verify orchestration-pipelines.md is gone**

```bash
ls docs/orchestration-pipelines.md 2>&1  # Should say "No such file"
```

**Step 3: Grep for stale references**

```bash
# No remaining references to the deleted file
grep -r "orchestration-pipelines" . --include="*.md" | grep -v docs/plans/
```

Expected: No output (or only in plan files which are historical).

**Step 4: Run tests to verify nothing is broken**

```bash
uv run pytest -v
uv run python manage.py check
```

**Step 5: Lint**

```bash
uv run black --check .
uv run ruff check .
```

---

## Files Summary

| Action | File |
|--------|------|
| Create | `docs/Architecture.md` |
| Delete | `docs/orchestration-pipelines.md` |
| Edit | `README.md` — trim to overview + links |
| Edit | `apps/alerts/README.md` — update cross-reference |
| Edit | `apps/checkers/README.md` — remove pipeline duplication |
| Edit | `apps/intelligence/README.md` — remove pipeline duplication |
| Edit | `apps/notify/README.md` — update cross-reference |
| Edit | `apps/orchestration/README.md` — remove pipeline intro duplication |
| Edit | `CLAUDE.md` — update doc references |
