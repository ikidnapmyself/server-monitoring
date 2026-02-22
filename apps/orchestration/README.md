# Orchestration App

> See [Architecture](../../docs/Architecture.md) for the full pipeline overview, entry points, and orchestration system comparison.

This app controls the lifecycle of pipeline runs through a strict state machine.

## Key Concepts

### State Machine

Every pipeline run goes through these statuses:

- `PENDING` → Initial state
- `INGESTED` → Alert ingested successfully
- `CHECKED` → Diagnostics completed
- `ANALYZED` → AI analysis completed
- `NOTIFIED` → Notifications sent (terminal success)
- `FAILED` → Pipeline failed (terminal failure)
- `RETRYING` → Pipeline is being retried
- `SKIPPED` → Stage was skipped

### Correlation IDs

Every pipeline run has:
- `trace_id` - Correlation ID for tracing across all stages and logs
- `run_id` - Unique ID for this specific pipeline run

These IDs are attached to all logs, monitoring events, DB records, and notifications.

### Stage Contracts

Each stage returns a structured DTO:

1. **IngestResult** - Alert parsing and incident creation
2. **CheckResult** - Diagnostic check results
3. **AnalyzeResult** - AI analysis and recommendations
4. **NotifyResult** - Notification delivery results

### Monitoring Signals

The orchestrator emits signals at every stage boundary:

- `pipeline.stage.started`
- `pipeline.stage.succeeded`
- `pipeline.stage.failed`
- `pipeline.stage.retrying`
- `pipeline.started`
- `pipeline.completed`

All signals include minimum tags: `trace_id`, `run_id`, `incident_id`, `stage`, `source`, `alert_fingerprint`, `environment`, `attempt`.

## API Endpoints

### Trigger Pipeline (Async)

```bash
POST /orchestration/pipeline/
Content-Type: application/json

{
    "payload": {
        "alertname": "HighCPU",
        "severity": "critical",
        ...
    },
    "source": "grafana",
    "environment": "production"
}
```

Response:
```json
{
    "status": "queued",
    "task_id": "abc123",
    "message": "Pipeline queued for execution"
}
```

### Trigger Pipeline (Sync)

```bash
POST /orchestration/pipeline/sync/
```

Waits for pipeline completion and returns full result.

### Get Pipeline Status

```bash
GET /orchestration/pipeline/<run_id>/
```

### List Pipelines

```bash
GET /orchestration/pipelines/?status=failed&limit=10
```

### Resume Failed Pipeline

```bash
POST /orchestration/pipeline/<run_id>/resume/
```

## Configuration

Set these environment variables (or in `config/settings.py`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | `3` | Max retries per stage before failing |
| `ORCHESTRATION_BACKOFF_FACTOR` | `2.0` | Exponential backoff factor |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | `1` | Enable fallback when AI fails |
| `ORCHESTRATION_METRICS_BACKEND` | `logging` | Metrics backend (`logging` or `statsd`) |
| `STATSD_HOST` | `localhost` | StatsD host (when using statsd backend) |
| `STATSD_PORT` | `8125` | StatsD port |
| `STATSD_PREFIX` | `pipeline` | StatsD metric prefix |

## CLI Reference

### `run_pipeline`

Execute the full pipeline (ingest → check → analyze → notify) or parts of it. All flags can be passed after aliases too (e.g., `sm-run-pipeline --sample --dry-run`).

```bash
# Run with sample alert payload (quickest test)
uv run python manage.py run_pipeline --sample

# Dry run: show what would happen without executing
uv run python manage.py run_pipeline --sample --dry-run
```

#### Payload sources

```bash
# Sample payload (built-in test data)
uv run python manage.py run_pipeline --sample

# From a JSON file
uv run python manage.py run_pipeline --file alert.json
uv run python manage.py run_pipeline --file /path/to/payload.json

# Inline JSON string
uv run python manage.py run_pipeline --payload '{"name": "Test Alert", "status": "firing", "severity": "warning"}'
```

#### Source format

```bash
# Specify the alert source format
uv run python manage.py run_pipeline --sample --source alertmanager
uv run python manage.py run_pipeline --sample --source grafana
uv run python manage.py run_pipeline --sample --source pagerduty
uv run python manage.py run_pipeline --sample --source generic
uv run python manage.py run_pipeline --file alert.json --source datadog
```

#### Environment and correlation

```bash
# Set environment name
uv run python manage.py run_pipeline --sample --environment production
uv run python manage.py run_pipeline --sample --environment staging

# Set custom trace ID for correlation
uv run python manage.py run_pipeline --sample --trace-id my-trace-123

# Both
uv run python manage.py run_pipeline --sample --environment production --trace-id deploy-v2.1.0
```

#### Partial execution

```bash
# Run only the checkers stage (skip alert ingestion)
uv run python manage.py run_pipeline --sample --checks-only

# Checks only + dry run
uv run python manage.py run_pipeline --sample --checks-only --dry-run
```

#### Notification driver

```bash
# Specify which notification driver to use
uv run python manage.py run_pipeline --sample --notify-driver slack
uv run python manage.py run_pipeline --sample --notify-driver email
uv run python manage.py run_pipeline --sample --notify-driver pagerduty
uv run python manage.py run_pipeline --sample --notify-driver generic
```

#### Definition-based pipelines

```bash
# Run a pipeline definition stored in the database
uv run python manage.py run_pipeline --definition my-pipeline-name

# Run from a JSON config file
uv run python manage.py run_pipeline --config path/to/pipeline.json

# Definition + environment
uv run python manage.py run_pipeline --definition production-pipeline --environment production
```

#### JSON output

```bash
uv run python manage.py run_pipeline --sample --json
uv run python manage.py run_pipeline --file alert.json --json
```

#### Combined examples

```bash
# Full production pipeline: file payload, production env, trace ID, slack notify, JSON
uv run python manage.py run_pipeline \
  --file alert.json \
  --source grafana \
  --environment production \
  --trace-id incident-2024-001 \
  --notify-driver slack \
  --json

# Quick smoke test: sample, dry run, JSON
uv run python manage.py run_pipeline --sample --dry-run --json

# Checks-only with custom source and trace
uv run python manage.py run_pipeline --sample --checks-only --source alertmanager --trace-id diag-run-1

# Definition pipeline with all options
uv run python manage.py run_pipeline \
  --definition my-pipeline \
  --environment staging \
  --trace-id test-run-42 \
  --notify-driver email \
  --json
```

#### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--sample` | flag | — | Use built-in sample alert payload |
| `--payload` | str | — | Inline JSON payload string |
| `--file` | str | — | Path to JSON payload file |
| `--source` | str | `cli` | Alert source format |
| `--environment` | str | `development` | Environment name |
| `--trace-id` | str | auto-generated | Custom trace ID for correlation |
| `--checks-only` | flag | — | Run only checkers stage |
| `--dry-run` | flag | — | Preview without executing |
| `--notify-driver` | str | `generic` | Notification driver to use |
| `--json` | flag | — | Output as JSON |
| `--definition` | str | — | Pipeline definition name (from DB) |
| `--config` | str | — | Path to pipeline definition JSON file |

---

### Definition-Based Pipelines — Deep Dive

The definition-based pipeline system lets you compose any combination of nodes into a custom pipeline. Unlike the hardcoded 4-stage pipeline, definition-based pipelines are configured via JSON and stored in the database.

#### Node Handlers Reference

##### `ingest` — Alert Ingestion

Parses incoming alert webhooks and creates Incident + Alert records in the database.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `driver` | string | auto-detect | Alert source format (alertmanager, grafana, pagerduty, etc.) |

**Output:**
```json
{
  "alerts_created": 1,
  "alerts_updated": 0,
  "alerts_resolved": 0,
  "incidents_created": 1,
  "incident_id": 42,
  "severity": "warning",
  "source": "grafana"
}
```

**Error behavior:** Fails the node if payload is invalid or ingestion raises an exception.

##### `context` — System Health Checks

Runs real system health checkers (CPU, memory, disk, network, process) and returns structured results.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `checker_names` | list[string] | all enabled | Which checkers to run. Omit to run all enabled checkers (respects `CHECKERS_SKIP`). |

**Output:**
```json
{
  "checks_run": 3,
  "checks_passed": 2,
  "checks_failed": 1,
  "results": {
    "cpu": {"status": "ok", "message": "CPU usage normal (12%)", "metrics": {"percent": 12.3}},
    "memory": {"status": "warning", "message": "Memory usage high (82%)", "metrics": {"percent": 82.1}},
    "disk": {"status": "ok", "message": "Disk usage normal", "metrics": {"percent": 45.0}}
  }
}
```

**Error behavior:** Individual checker failures do NOT fail the node. A failing checker is recorded with `"status": "unknown"` and its error message. The node only fails if zero valid checkers can be resolved.

##### `intelligence` — AI Analysis

Generates AI-powered recommendations using the configured provider (local rule-based or OpenAI).

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `provider` | string | **required** | Provider name: `local` or `openai` |
| `provider_config` | object | `{}` | Provider-specific config (e.g., `{"model": "gpt-4o-mini"}` for OpenAI) |

**Output:**
```json
{
  "provider": "local",
  "recommendations": [
    {"title": "High memory usage", "description": "Consider restarting workers", "priority": "high"}
  ],
  "count": 1,
  "summary": "High memory usage"
}
```

**Error behavior:** Fails the node on exception. Use `"required": false` in the pipeline definition to make AI analysis optional — the pipeline will continue even if this node fails.

##### `notify` — Send Notifications

Sends notifications via database-configured `NotificationChannel` records. Builds a smart notification message from all previous node outputs.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `drivers` | list[string] | — | Driver types to send via (e.g., `["slack", "email"]`) |
| `driver` | string | — | Single driver type (backwards compat, use `drivers` for new pipelines) |

**Output:**
```json
{
  "channels_attempted": 2,
  "channels_succeeded": 1,
  "channels_failed": 1,
  "deliveries": [
    {"driver": "slack", "channel": "ops-alerts", "status": "success", "message_id": "msg-123"},
    {"driver": "email", "channel": "ops-email", "status": "failed", "error": "SMTP timeout"}
  ]
}
```

**Error behavior:** Individual channel failures do NOT fail the node. The node only adds an error if ALL channels fail. Partial success (some channels delivered, some failed) is considered success at the node level.

**Prerequisite:** You must have at least one active `NotificationChannel` in the database matching the configured driver type. Create channels via:
- `python manage.py setup_instance` (interactive wizard)
- Django Admin at `/admin/notify/notificationchannel/`

##### `transform` — Data Transformation

Extracts, filters, or maps data from a previous node's output.

| Config Key | Type | Default | Description |
|------------|------|---------|-------------|
| `source_node` | string | **required** | ID of the node whose output to transform |
| `extract` | string | — | Dot-notation path to extract (e.g., `"recommendations"`) |
| `filter_priority` | string | — | Filter list items by priority field |
| `mapping` | object | — | Map fields using dot-notation paths (e.g., `{"cpu": "results.cpu.metrics.percent"}`) |

**Output:**
```json
{
  "transformed": { ... },
  "source_node": "check_health"
}
```

#### Node Output Chaining

Each node's output is automatically stored in `NodeContext.previous_outputs` keyed by the node's `id`. All downstream nodes can access any upstream node's output.

```
Pipeline: context("check_health") → intelligence("analyze") → notify("notify_channels")

check_health runs → output stored as previous_outputs["check_health"]
                ↓
analyze runs   → can read previous_outputs["check_health"]
               → output stored as previous_outputs["analyze"]
                ↓
notify runs    → can read previous_outputs["check_health"] AND previous_outputs["analyze"]
               → uses both to build notification message
```

The `notify` node uses this to build smart notification messages:

1. **From checker results:** Derives severity (critical > warning > info), lists failed checks, generates title like "Health Check Alert — Critical"
2. **From intelligence results:** Includes AI summary, probable cause, and recommendation count
3. **No previous outputs:** Falls back to "Pipeline completed."

#### Building a Custom Pipeline — Tutorial

**1. Decide which nodes you need**

| Use case | Nodes |
|----------|-------|
| Local health monitoring | `context` → `notify` |
| Local monitoring with AI | `context` → `intelligence` → `notify` |
| External alert forwarding | `ingest` → `notify` |
| Full pipeline | `ingest` → `context` → `intelligence` → `notify` |

**2. Write the pipeline definition JSON**

```json
{
  "version": "1.0",
  "description": "Local health check with Slack notifications",
  "nodes": [
    {
      "id": "check_health",
      "type": "context",
      "config": {"checker_names": ["cpu", "memory", "disk"]},
      "next": "notify_channels"
    },
    {
      "id": "notify_channels",
      "type": "notify",
      "config": {"drivers": ["slack"]}
    }
  ]
}
```

Key rules:
- Each node needs a unique `id`
- Chain nodes with `"next": "<next_node_id>"` (omit on the last node)
- The first node in the array is the entry point

**3. Create a notification channel**

The `notify` node sends via `NotificationChannel` records in the database. Create one:

```bash
# Interactive wizard (creates both pipeline and channels)
uv run python manage.py setup_instance

# Or via Django Admin
# Go to /admin/notify/notificationchannel/ and add a channel
```

**4. Validate with dry-run**

```bash
# From a JSON file
uv run python manage.py run_pipeline --config my-pipeline.json --dry-run

# Or save to database first, then
uv run python manage.py run_pipeline --definition my-pipeline --dry-run
```

Expected output shows node chain and config without executing:
```
=== DRY RUN ===
Pipeline Config: my-pipeline.json
Source: cli
Environment: development

Nodes (2):
  1. [context] check_health
     Config: {"checker_names": ["cpu", "memory", "disk"]}
     → next: notify_channels

  2. [notify] notify_channels
     Config: {"drivers": ["slack"]}
     → end
```

**5. Run for real**

```bash
uv run python manage.py run_pipeline --config my-pipeline.json
```

Expected output:
```
Starting pipeline...
  Source: cli
  Environment: development

============================================================
PIPELINE RESULT
============================================================

Status: completed
Definition: my-pipeline
Trace ID: abc123-...
Run ID: def456-...
Duration: 1523.45ms

--- context (check_health) ---
  Checks run: 3
  Passed: 2
  Failed: 1
  cpu: ok — CPU usage normal (12%)
  memory: warning — Memory usage high (82%)
  disk: ok — Disk usage normal
  Duration: 1200.00ms

--- notify (notify_channels) ---
  Channels attempted: 1
  Succeeded: 1
  Failed: 0
  slack (ops-alerts): sent
  Duration: 323.45ms

✓ Pipeline completed successfully
```

#### Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `Checks run: N/A` in output | Wrong config key in pipeline definition | Use `"checker_names"`, not `"include"` |
| `No active NotificationChannel found` | No matching channel in database | Run `setup_instance` or create channel in Django Admin |
| `Unknown driver: <name>` | Channel's driver not in DRIVER_REGISTRY | Use a valid driver: `slack`, `email`, `pagerduty`, `generic` |
| `All N notification channel(s) failed` | Every channel failed to send | Check channel config (webhook URLs, SMTP settings) and driver logs |
| `No valid checkers to run` | All checker names in config are unknown | Check available checkers with `python manage.py check_health --list` |
| Pipeline shows `completed` but no notification received | Channel config is wrong (e.g., bad webhook URL) | Check `deliveries` array in output — look for `"status": "failed"` with error details |
| Intelligence node fails but pipeline continues | Node has `"required": false` | This is expected — optional nodes don't fail the pipeline |

---

### `monitor_pipeline`

View and monitor pipeline run history.

```bash
# List recent pipeline runs (default: last 10)
uv run python manage.py monitor_pipeline

# Show more runs
uv run python manage.py monitor_pipeline --limit 25
uv run python manage.py monitor_pipeline --limit 50
uv run python manage.py monitor_pipeline --limit 100
```

#### Filter by status

```bash
# Show only failed runs
uv run python manage.py monitor_pipeline --status failed

# Show only completed runs
uv run python manage.py monitor_pipeline --status notified

# Other statuses
uv run python manage.py monitor_pipeline --status pending
uv run python manage.py monitor_pipeline --status ingested
uv run python manage.py monitor_pipeline --status checked
uv run python manage.py monitor_pipeline --status analyzed
uv run python manage.py monitor_pipeline --status retrying
uv run python manage.py monitor_pipeline --status skipped
```

#### Inspect a specific run

```bash
# Get full details for a pipeline run by run_id
uv run python manage.py monitor_pipeline --run-id abc123
uv run python manage.py monitor_pipeline --run-id 550e8400-e29b-41d4-a716-446655440000
```

#### Combined examples

```bash
# Last 50 failed runs
uv run python manage.py monitor_pipeline --status failed --limit 50

# Last 20 completed runs
uv run python manage.py monitor_pipeline --status notified --limit 20
```

#### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--limit` | int | `10` | Number of pipeline runs to show |
| `--status` | str | all | Filter by status (pending, ingested, checked, analyzed, notified, failed, retrying, skipped) |
| `--run-id` | str | — | Show details for a specific pipeline run |

### Python API

```python
from apps.orchestration.orchestrator import PipelineOrchestrator

# Run pipeline synchronously
orchestrator = PipelineOrchestrator()
result = orchestrator.run_pipeline(
    payload={"payload": alert_data},
    source="grafana",
    trace_id="custom-trace-123",
)

print(f"Pipeline {result.status}: {result.stages_completed}")
```

### Celery Tasks

```python
from apps.orchestration.tasks import run_pipeline_task, start_pipeline_task

# Fire-and-forget
start_pipeline_task.delay(
    payload={"payload": alert_data},
    source="webhook",
)

# Or run directly
result = run_pipeline_task.apply(
    args=[{"payload": alert_data}],
    kwargs={"source": "manual"},
)
```

## Intelligence Fallback

When `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED=1` (default), if the AI analysis stage fails, the pipeline will:

1. Continue to the notify stage
2. Send a notification with "AI analysis unavailable"
3. Record `intelligence_fallback_used=True` in the pipeline run

This ensures critical alerts are still communicated even when AI is down.

## Models

### PipelineRun

Represents a single pipeline execution with:
- Correlation IDs (trace_id, run_id)
- State machine status
- Link to incident
- References to stage outputs
- Error tracking
- Timestamps and duration

### StageExecution

Tracks individual stage executions with:
- Stage identifier
- Attempt number (for retries)
- Idempotency key
- Input/output references
- Error details
- Timing information

## Testing

Run orchestration tests:

```bash
uv run pytest apps/orchestration/_tests/ -v
```

## Admin Interface

The orchestration models are registered in Django admin:

- View and filter pipeline runs by status, source, stage
- Inspect stage execution details inline
- View error information

Access at `/admin/orchestration/`.

