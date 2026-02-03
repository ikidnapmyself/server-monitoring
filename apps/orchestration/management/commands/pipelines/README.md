# Sample Pipeline Definitions

This directory contains sample pipeline definition files for use with `run_pipeline --config`.

## Available Pipelines

### pipeline-manager.json
**Use case:** Central pipeline manager receiving alerts from multiple external servers.

Flow: `ingest → intelligence → notify` (skips checkers)

This pipeline:
- Receives alerts via webhook from external monitoring systems
- Analyzes alerts using AI (OpenAI/local provider)
- Sends notifications to configured channels
- Does NOT run local health checks (checkers are skipped)

Usage:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --file alert.json
```

### local-monitor.json
**Use case:** Full monitoring pipeline for the local server.

Flow: `ingest → context → intelligence → notify`

This pipeline:
- Ingests alerts (can be from local `check_and_alert` or external webhook)
- Gathers local system context (CPU, memory, disk)
- Analyzes with AI including local metrics
- Sends notifications

Usage:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --sample
```

## Creating Custom Pipelines

Copy one of the samples and modify the `nodes` array. Each node requires:
- `id`: Unique identifier
- `type`: One of `ingest`, `context`, `intelligence`, `notify`, `transform`
- `config`: Type-specific configuration
- `next`: (Optional) Next node ID

See `apps/orchestration/nodes/` for available node types and their configs.

## Example Alert Payloads

### Grafana Alert
```json
{
  "alertname": "HighCPU",
  "severity": "critical",
  "instance": "web-server-01",
  "description": "CPU usage above 90% for 5 minutes"
}
```

Save as `alert.json` and run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager.json --file alert.json
```

### Alertmanager Alert
```json
{
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "DiskSpaceLow",
        "severity": "warning",
        "instance": "db-server-01"
      },
      "annotations": {
        "description": "Disk usage above 85%"
      }
    }
  ]
}
```

### PagerDuty Alert (Webhook)
```json
{
  "event": {
    "id": "01ABCDEF",
    "event_type": "incident.triggered",
    "resource_type": "incident",
    "occurred_at": "2024-01-15T10:30:00.000Z",
    "data": {
      "id": "P123ABC",
      "type": "incident",
      "title": "High CPU on web-server-01",
      "status": "triggered",
      "urgency": "high",
      "service": {
        "id": "PABCDEF",
        "name": "Production Web Servers"
      },
      "body": {
        "details": {
          "cpu_percent": 95,
          "process": "nginx",
          "duration_minutes": 10
        }
      }
    }
  }
}
```

Save as `pagerduty-alert.json` and run:
```bash
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pagerduty-alert.json --file pagerduty-alert.json
```

## Alert Context Flow to Intelligence Providers

When the pipeline processes an alert, context flows to the intelligence/analyze step:

1. **Ingest Node**: Parses the alert, creates `Incident` and `Alert` records in DB
   - Stores original payload in `Alert.raw_payload`
   - Returns `incident_id`, `severity`, `source`

2. **Intelligence Node**: Receives context via:
   - `ctx.incident_id` → Fetches full `Incident` from DB
   - `incident.alerts` → Access all related alerts with `raw_payload`
   - `ctx.payload` → Original webhook payload
   - `ctx.previous_outputs["ingest"]` → Ingest node output

3. **Provider receives**: Full `Incident` object with all alert details

For 3rd party intelligence providers (OpenAI, etc.), the provider's `analyze(incident)` method receives the complete incident, enabling context-aware analysis.

## Environment Variables

For OpenAI-based pipelines:
```bash
export OPENAI_API_KEY="your-api-key"
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/pipeline-manager-openai.json --file alert.json
```

## Notification Drivers

Change the `notify` node's `driver` config to use different notification backends:
- `generic` - HTTP webhook (default)
- `slack` - Slack webhook
- `email` - Email via SMTP
- `pagerduty` - PagerDuty Events API

Example with Slack:
```json
{
  "id": "notify",
  "type": "notify",
  "config": {
    "driver": "slack"
  }
}
```
