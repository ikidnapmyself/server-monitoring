# Orchestration App

The orchestration app controls the full lifecycle of an incident through a strict, linear pipeline:

```
apps.alerts → apps.checkers → apps.intelligence → apps.notify
```

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

## Usage Examples

### Management Command

The easiest way to test the pipeline end-to-end:

```bash
# Dry run with sample alert (shows what would happen)
uv run python manage.py run_pipeline --sample --dry-run

# Run with sample alert
uv run python manage.py run_pipeline --sample

# Run with specific source format
uv run python manage.py run_pipeline --sample --source alertmanager
uv run python manage.py run_pipeline --sample --source grafana

# Run with custom payload from file
uv run python manage.py run_pipeline --file alert.json

# Run with inline JSON payload
uv run python manage.py run_pipeline --payload '{"name": "Test", "status": "firing", "severity": "warning"}'

# Output as JSON
uv run python manage.py run_pipeline --sample --json

# Set environment and trace ID for correlation
uv run python manage.py run_pipeline --sample --environment production --trace-id my-trace-123

# Run only the checkers stage (skip alert ingestion)
uv run python manage.py run_pipeline --sample --checks-only

# Specify notification driver
uv run python manage.py run_pipeline --sample --notify-driver slack

# Run a definition-based pipeline (from database)
uv run python manage.py run_pipeline --definition my-pipeline-name

# Run a pipeline from a JSON config file
uv run python manage.py run_pipeline --config path/to/pipeline.json
```

### Monitor Pipeline Command

View and monitor pipeline runs:

```bash
# List recent pipeline runs (default: 10)
uv run python manage.py monitor_pipeline

# List more runs
uv run python manage.py monitor_pipeline --limit 50

# Filter by status
uv run python manage.py monitor_pipeline --status failed
uv run python manage.py monitor_pipeline --status completed

# Get details for a specific run
uv run python manage.py monitor_pipeline --run-id abc123
```

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

