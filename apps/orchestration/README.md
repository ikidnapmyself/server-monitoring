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
uv run pytest apps/orchestration/tests.py -v
```

## Admin Interface

The orchestration models are registered in Django admin:

- View and filter pipeline runs by status, source, stage
- Inspect stage execution details inline
- View error information

Access at `/admin/orchestration/`.

