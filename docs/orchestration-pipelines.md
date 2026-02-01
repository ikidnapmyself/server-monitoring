# Orchestration Pipeline Systems

This document explains the two pipeline execution systems and when to use each.

## Overview

The orchestration app provides two ways to execute pipelines:

| System | Endpoint | Configuration | Use Case |
|--------|----------|---------------|----------|
| **Hardcoded** | `POST /orchestration/pipeline/` | Code-defined stages | Existing webhook integrations |
| **Definition-based** | `POST /orchestration/definitions/<name>/execute/` | JSON in database | Flexible, configurable pipelines |

Both systems create `PipelineRun` and `StageExecution` records for tracking.

---

## Hardcoded Pipeline

**Location:** `apps/orchestration/orchestrator.py`

**Fixed stages:** INGEST → CHECK → ANALYZE → NOTIFY

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ INGEST  │───▶│  CHECK  │───▶│ ANALYZE │───▶│ NOTIFY  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
IngestExecutor CheckExecutor AnalyzeExecutor NotifyExecutor
```

### Endpoints

```bash
# Async execution (queued via Celery)
POST /orchestration/pipeline/
{
  "payload": {...},
  "source": "grafana",
  "trace_id": "optional-correlation-id",
  "environment": "production"
}

# Sync execution (waits for completion)
POST /orchestration/pipeline/sync/
```

### When to use

- Existing webhook integrations (Grafana, AlertManager, Prometheus)
- Standard alert processing flow
- When you need the full INGEST → CHECK → ANALYZE → NOTIFY sequence

---

## Definition-Based Pipeline

**Location:** `apps/orchestration/definition_orchestrator.py`

**Dynamic stages:** Configured via JSON stored in `PipelineDefinition` model

```
┌──────────────────────────────────────────────────────────┐
│                   PipelineDefinition                     │
│  {                                                       │
│    "nodes": [                                            │
│      {"id": "a", "type": "context", ...},                │
│      {"id": "b", "type": "intelligence", ...},           │
│      {"id": "c", "type": "notify", ...}                  │
│    ]                                                     │
│  }                                                       │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
              DefinitionBasedOrchestrator
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
      ContextNode   IntelligenceNode   NotifyNode
```

### Available Node Types

| Type | Handler | Purpose |
|------|---------|---------|
| `ingest` | IngestNodeHandler | Process incoming alerts, create incidents |
| `context` | ContextNodeHandler | Gather system metrics (CPU, memory, disk) |
| `intelligence` | IntelligenceNodeHandler | AI analysis (local or OpenAI) |
| `notify` | NotifyNodeHandler | Send notifications (Slack, email, PagerDuty) |
| `transform` | TransformNodeHandler | Transform data between nodes |

### Endpoints

```bash
# List definitions
GET /orchestration/definitions/

# Get definition details
GET /orchestration/definitions/<name>/

# Validate a definition
POST /orchestration/definitions/<name>/validate/

# Execute a definition
POST /orchestration/definitions/<name>/execute/
{
  "payload": {...},
  "source": "api",
  "environment": "production"
}
```

### When to use

- Custom pipeline configurations
- Standalone health checks (no alert trigger)
- Chaining multiple AI providers
- Pipelines without all four stages
- Quick iteration without code changes

---

## Example Configurations

### Alert-Triggered Pipeline
```json
{
  "version": "1.0",
  "nodes": [
    {"id": "ingest", "type": "ingest", "next": "analyze"},
    {"id": "analyze", "type": "intelligence", "config": {"provider": "openai"}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack"}}
  ]
}
```

### Standalone Health Check
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

### Simple Metrics Alert (No AI)
```json
{
  "version": "1.0",
  "nodes": [
    {"id": "metrics", "type": "context", "next": "transform"},
    {"id": "transform", "type": "transform", "config": {"source_node": "metrics", "mapping": {"cpu": "context.cpu.load"}}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"driver": "slack"}}
  ]
}
```

---

## Creating Definitions

### Via Django Admin

1. Go to `/admin/orchestration/pipelinedefinition/`
2. Click "Add Pipeline Definition"
3. Enter name, description, and JSON config
4. Save

### Via API (future)

Pipeline definition CRUD endpoints can be added if needed.

---

## Comparison

| Feature | Hardcoded | Definition-based |
|---------|-----------|------------------|
| Configuration | Python code | JSON in database |
| Stages | Fixed 4 stages | Any combination of nodes |
| Deployment | Code deploy required | Admin UI or API |
| Retry logic | Built-in per stage | Built-in per node |
| Celery support | Yes (async mode) | Not yet (sync only) |
| Resume failed | Yes | Not yet |

---

## Data Flow

Both systems share:
- `PipelineRun` model for tracking executions
- `StageExecution` model for stage/node tracking
- Same status values: PENDING, INGESTED, CHECKED, ANALYZED, NOTIFIED, FAILED

The hardcoded system uses `PipelineStage` enum values, while definition-based uses node type strings. Both are stored in the same `stage` field.
