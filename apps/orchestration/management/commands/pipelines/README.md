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

## Sample Definitions vs `setup_instance` Wizard

There are two ways to create pipeline definitions:

| | Sample Definitions | `setup_instance` Wizard |
|---|---|---|
| **What** | Static JSON files in this directory | Interactive CLI that creates DB records |
| **Where** | Files on disk | `PipelineDefinition` + `NotificationChannel` in database |
| **Run with** | `--config pipelines/local-monitor.json` | `--definition my-pipeline` |
| **Notification channels** | Uses placeholder drivers (e.g., `"driver": "generic"`) | Creates real `NotificationChannel` records with your credentials |
| **Best for** | Testing, learning, starting points | Production setup |

**Quick start with samples:**
```bash
# Test a sample pipeline (uses generic driver, won't actually send notifications)
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --sample --dry-run

# Set up a real pipeline with your notification credentials
uv run python manage.py setup_instance
```

### What each sample does

#### pipeline-manager.json — Alert Hub (no local checks)

```
ingest → intelligence → notify
```

For a central server that receives alerts from other monitoring systems. Skips local health checks because this server isn't what's being monitored — it just processes alerts from elsewhere.

- **Ingest:** Parses incoming webhook, creates Incident/Alert records
- **Intelligence:** Analyzes the alert with AI (local provider)
- **Notify:** Forwards notification via generic webhook

#### pipeline-manager-openai.json — Alert Hub with OpenAI

Same as `pipeline-manager.json` but uses OpenAI instead of the local provider. Requires `OPENAI_API_KEY` environment variable.

#### local-monitor.json — Full Local Monitoring

```
ingest → context → intelligence → notify
```

For monitoring the server this app runs on. Runs real system health checks (CPU, memory, disk), analyzes results with AI, and sends notifications.

- **Context:** Runs CPU, memory, and disk checkers via `checker_names` config
- **Intelligence:** Optional (`"required": false`) — pipeline continues even if AI fails
- **Notify:** Sends via generic driver

#### pagerduty-alert.json — PagerDuty Integration

```
ingest → intelligence → notify
```

Specialized for PagerDuty webhooks. The `ingest` node has `"source_hint": "pagerduty"` to help alert parsing. Intelligence uses OpenAI with a system prompt tuned for PagerDuty incident context. Notify sends back to PagerDuty with `"include_recommendations": true`.

Includes a `context_flow` comment documenting how PagerDuty webhook data flows through the pipeline:
1. Ingest parses webhook → creates DB records
2. Intelligence fetches Incident from DB → gets full alert context
3. Notify sends analysis back to PagerDuty

## Creating Custom Pipelines

Copy one of the samples and modify the `nodes` array. Each node requires:
- `id`: Unique identifier for this node
- `type`: One of `ingest`, `context`, `intelligence`, `notify`, `transform`
- `config`: Type-specific configuration (see [orchestration README](../../README.md) for config reference)
- `next`: (Optional) Next node ID in the chain

For a full tutorial with step-by-step instructions, see the **Building a Custom Pipeline** section in `apps/orchestration/README.md`.

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

Change the `notify` node's `driver` or `drivers` config to use different notification backends:
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
    "drivers": ["slack"]
  }
}
```

**Important:** The `notify` node looks up active `NotificationChannel` records in the database matching the configured driver type. You must create a channel first — either via `setup_instance` wizard or Django Admin.
