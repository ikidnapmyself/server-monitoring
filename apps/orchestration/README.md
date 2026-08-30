# Orchestration App

> See [Architecture](../../docs/Architecture.md) for the full pipeline overview, entry points, and the routing model.

This app controls the lifecycle of pipeline runs through a strict state machine.

## Key Concepts

### State Machine

Every pipeline run goes through these statuses:

- `PENDING` → Initial state
- `INGESTED` → the status floor a run starts from (**not** evidence an INGEST stage ran;
  producers write alerts, and INGEST is retired as a stage)
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

1. **CheckResult** - Diagnostic check results
2. **AnalyzeResult** - AI analysis and recommendations
3. **NotifyResult** - Notification delivery results

`IngestResult` still exists but is built only by the legacy `IngestExecutor`, which drains
runs whose payload is a legacy `{"driver", "payload"}` pair.

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

The payload is ingested on the request thread — producing an alert is not a pipeline
stage — and one `PENDING` run per materially changed incident is left for the
`process_inbox` drain. There is no single `run_id` to return: a call can produce several
runs or none, so the response names the trace and the incidents.

Response (202):
```json
{
    "status": "accepted",
    "trace_id": "…",
    "incidents": [12, 13]
}
```

A payload no driver claims returns `400` (a retry would fail identically). An ingest
that breaks after a driver resolved, having written nothing, returns `500` so the sender
retries. Errors alongside written alerts are logged and still accepted.

### Trigger Pipeline (Sync)

```bash
POST /orchestration/pipeline/sync/
```

The same producer path, but the runs it enqueued are drained before responding.

Response (200):
```json
{
    "status": "completed",
    "trace_id": "…",
    "incidents": [12],
    "alerts": 1,
    "errors": []
}
```

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

**Replay an alert payload.** `run_pipeline` is a producer like the webhook: it ingests the
payload here (`AlertOrchestrator.process_webhook`), then enqueues one run per materially
changed incident and drains them before returning — `enqueue_for(origin=manual, sync=True)`.
All flags can be passed after aliases too (e.g., `sm-run-pipeline --sample --dry-run`).

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

#### Running this machine's checkers

```bash
# The local entrypoint — see apps/checkers/README.md
uv run python manage.py check_health
```

`run_pipeline --checks-only` does the same work and is **deprecated**: it prints a notice
on stderr on every run. Note it ignores `--sample` and any other payload flag — with
`--checks-only` there is no payload to replay.

#### Notification driver

```bash
# Specify which notification driver to use
uv run python manage.py run_pipeline --sample --notify-driver slack
uv run python manage.py run_pipeline --sample --notify-driver email
uv run python manage.py run_pipeline --sample --notify-driver pagerduty
uv run python manage.py run_pipeline --sample --notify-driver generic
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

# Custom source and trace
uv run python manage.py run_pipeline --sample --source alertmanager --trace-id diag-run-1
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
| `--checks-only` | flag | — | **DEPRECATED** — use `check_health`. Run this machine's checkers instead of replaying a payload; prints a notice on stderr |
| `--checkers` | str... | all | With `--checks-only`: which checkers to run |
| `--hostname` | str | — | With `--checks-only`: label the alerts for another machine (and skip local Node registration) |
| `--label` | `KEY=VALUE` | — | With `--checks-only`: extra alert label, repeatable |
| `--warning-threshold` | float | per-checker | With `--checks-only`: threshold override |
| `--critical-threshold` | float | per-checker | With `--checks-only`: threshold override |
| `--no-incidents` | flag | — | Record alerts but create no incidents, so nothing is enqueued, routed, analysed or notified |
| `--no-notify` | flag | — | Run the matched lane without NOTIFY; travels in the enqueued run's payload |
| `--dry-run` | flag | — | Preview without executing (plain text; ignores `--json`) |
| `--notify-driver` | str | `generic` | **Accepted and currently unused** — delivery is chosen by the matched lane's channel |
| `--json` | flag | — | Output as JSON: `{trace_id, incidents, alerts, errors}` |

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

Be a producer: write the alerts, then hand the materially changed incidents to intake.
This is what the webhook and every command do.

```python
import uuid

from apps.alerts.services import AlertOrchestrator
from apps.orchestration.intake import enqueue_for
from apps.orchestration.models import PipelineOrigin

trace_id = str(uuid.uuid4())
result = AlertOrchestrator(trace_id=trace_id).process_webhook(alert_data, driver="grafana")

# One PENDING run per materially changed incident. sync=True drains them here;
# without it, process_inbox takes them.
runs = enqueue_for(
    result,
    trace_id=trace_id,
    origin=PipelineOrigin.MANUAL,
    source="grafana",
    sync=True,
)
print([run.incident_id for run in runs])
```

Do not call the entry-stage path (`start_pipeline` / `run_pipeline()` with a
`{"driver", "payload"}` payload). It exists only to drain runs recorded before this
model, and it will go away.

### Durable ingest / drain (broker-free)

The webhook writes the alerts inline and records one `PENDING` run per materially changed
incident (`inbound_payload = {"downstream_incident_id": N}`), which a drain picks up later.
Use `enqueue_for` without `sync` to do the same thing; to execute one run by hand:

```python
from apps.orchestration.orchestrator import PipelineOrchestrator

# What manage.py process_inbox does in a loop, per claimed run:
result = PipelineOrchestrator().execute_run(run)
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

