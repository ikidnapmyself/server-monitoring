---
title: Setup Guide
layout: default
nav_order: 4
---

# Setup Guide

This guide walks you through configuring your server-maintanence instance after installation.
Each section is a complete walkthrough for a specific use case — pick the one that matches
your situation and follow it end-to-end.

[toc]

---

## Prerequisites

Before starting, make sure you have completed the installation:

```bash
./bin/install.sh
```

Verify everything is working:

```bash
bin/check_system.sh                    # Full system check (shell + Django)
uv run python manage.py preflight      # Django-only preflight checks
uv run python manage.py check_health   # Health checks run without errors
```

If any command fails, see [Installation.md](Installation.md) for troubleshooting.

---

## Choose Your Use Case

| I want to... | Preset | Alert source | Stages |
|---|---|---|---|
| Monitor this server (basic) | `local-monitor` | Local crontab | Checkers → Notify |
| Monitor this server (with AI) | `local-smart` | Local crontab | Checkers → Intelligence → Notify |
| Forward alerts to notifications | `direct` | External webhooks | Alert → Notify |
| Forward alerts with health context | `health-checked` | External webhooks | Alert → Checkers → Notify |
| Forward alerts with AI analysis | `ai-analyzed` | External webhooks | Alert → Intelligence → Notify |
| Full alert processing pipeline | `full` | External webhooks | Alert → Checkers → Intelligence → Notify |

Not sure which to pick? Start with **Use Case 1** (local monitoring) — it requires no external
services and you can see results immediately.

---

## Use Case 1: Monitor This Server Locally

**Scenario:** You want to monitor CPU, memory, disk, and network on this machine and get
notified when something is wrong. No external monitoring tools required.

### Step 1: Run the setup wizard

```bash
uv run python manage.py setup_instance
```

### Step 2: Select alert source

```
? How will alerts be generated?
  1) External webhooks  (Grafana, PagerDuty, etc.)
  2) Local crontab  (run_pipeline --checks-only via cron)

> 2
```

Pick **Local crontab**. This means health checks run on a schedule via cron and generate
alerts locally — no external webhook source needed.

### Step 3: Select pipeline preset

```
? How will you use this instance?
  1) Checkers → Notify  (Local health monitoring)
  2) Checkers → Intelligence → Notify  (Local monitoring with AI)

> 1
```

Pick **Checkers → Notify** for basic monitoring. If you have an AI provider API key and want
AI-powered analysis of issues, pick option 2 instead.

### Step 4: Configure checkers

The wizard detects your OS and pre-selects sensible defaults. Checkers that don't apply to
your platform are hidden automatically (e.g., `disk_linux` won't appear on macOS).

```
--- Stage: Checkers ---
  Detected platform: macOS
? Which health checkers do you want to enable?
  * 1) cpu
  * 2) memory
  * 3) disk
  * 4) disk_common
  * 5) disk_macos
    6) network
    7) process

> (comma-separated, default: 1,2,3,4,5):
```

Press **Enter** to accept the defaults, or type specific numbers to customize.
Add `network` or `process` if you need connectivity or process monitoring.

If you select specific checkers, the wizard may ask follow-up questions:

- **disk** → "Disk paths to monitor" (default: `/`)
- **network** → "Hosts to ping" (default: `8.8.8.8,1.1.1.1`)
- **process** → "Process names to watch" (required — e.g., `nginx,postgres`)

### Step 5: Configure notifications

If you have existing notification channels in the database, the wizard lets you reuse them:

```
--- Stage: Notify ---
  Found 1 existing notification channel(s):
    - ops-slack (slack)

? Use existing channels, create new ones, or both?
  1) Use existing — Select from channels above
  2) Create new — Configure new channels from scratch
  3) Both — Select existing + add new ones

> 1

? Which existing channels do you want to use?
  * 1) ops-slack (slack)

> (comma-separated, default: 1):
```

If no existing channels are found, you go straight to creating new ones:

```
? Which notification channels do you want to configure?
  1) slack
  2) pagerduty
  3) email
  4) generic

> 4
```

Each driver asks for its own configuration:

| Driver | Required config |
|---|---|
| `slack` | Webhook URL |
| `email` | SMTP host, port, user, password, from address, to address |
| `pagerduty` | Routing key |
| `generic` | Endpoint URL (and optional headers) |

**Tip:** Start with `generic` if you just want to see output. It sends a POST request to any
HTTP endpoint. You can use a service like [webhook.site](https://webhook.site) for testing.

### Step 6: Review and apply

```
--- Summary ---
  Alert source: Local crontab
  Pipeline: Checkers → Notify
  Checkers: cpu, memory, disk
  Notification: generic (ops-generic)

? Apply this configuration? [Y/n]: Y
```

The wizard creates (or updates if re-running):
- A `PipelineDefinition` named `local-monitor` in the database
- A `NotificationChannel` for each notification driver you configured
- An `IntelligenceProvider` record if an AI provider was selected (with API key and model stored in the DB)

### Step 7: Test your notification channels

Before running the full pipeline, verify that notifications are working:

```bash
uv run python manage.py test_notify
```

The interactive wizard lists the channels you just configured, lets you send a test
notification, and retry with different options if something isn't right.

### Step 8: Verify with a dry run

```bash
uv run python manage.py run_pipeline --definition local-monitor --dry-run
```

This shows the node chain and configuration without executing anything. Verify the nodes
and config look correct.

### Step 9: Run your first pipeline

```bash
uv run python manage.py run_pipeline --definition local-monitor
```

You should see output showing each node executing in sequence: health checks run, results
are collected, and a notification is sent through your configured channel.

### Step 10: Set up recurring monitoring with cron

To run checks automatically on a schedule:

```bash
./bin/install.sh cron
```

The script lets you pick a schedule (every 5 minutes, 15 minutes, hourly, or custom) and
writes a crontab entry that runs:

```bash
uv run python manage.py run_pipeline --checks-only --json
```

This command runs all enabled checkers, creates alerts for any issues found, and optionally
creates incidents for critical problems. Output is logged to `cron.log` in the project root.

Verify cron is set up:

```bash
crontab -l               # See the cron entry
tail -f ./cron.log        # Watch output in real time
```

---

## Use Case 2: Process External Alerts (Full Pipeline)

**Scenario:** You receive alert webhooks from monitoring tools like Grafana, AlertManager,
or PagerDuty. You want to enrich them with local health checks, analyze with AI, and forward
notifications.

### Step 1: Run the setup wizard

```bash
uv run python manage.py setup_instance
```

### Step 2: Select alert source

```
? How will alerts be generated?
  1) External webhooks  (Grafana, PagerDuty, etc.)
  2) Local crontab  (run_pipeline --checks-only via cron)

> 1
```

Pick **External webhooks**.

### Step 3: Select pipeline preset

```
? How will you use this instance?
  1) Alert → Notify  (Direct forwarding)
  2) Alert → Checkers → Notify  (Health-checked alerts)
  3) Alert → Intelligence → Notify  (AI-analyzed alerts)
  4) Alert → Checkers → Intelligence → Notify  (Full pipeline)

> 4
```

Pick **Full pipeline** to use all stages. You can always pick a simpler preset if you don't
need every stage.

### Step 4: Configure alert drivers

```
--- Stage: Alerts ---
? Which alert drivers do you want to enable?
  1) alertmanager
  2) grafana
  3) pagerduty
  4) datadog
  5) newrelic
  6) opsgenie
  7) zabbix
  8) generic

> 2
```

Select the drivers that match your monitoring tools. The system auto-detects the driver from
incoming webhook payloads, so enabling multiple drivers is safe.

### Step 5: Configure checkers

Same as Use Case 1, Step 4. The wizard detects your OS, filters out irrelevant
platform-specific checkers, and pre-selects sensible defaults. Press Enter to accept or
customize.

### Step 6: Configure intelligence

```
--- Stage: Intelligence ---
? Which AI provider do you want to use?
  1) local
  2) openai
  3) claude
  4) gemini
  5) copilot
  6) grok
  7) ollama
  8) mistral

> 1
```

Pick your AI provider:

| Provider | Best for |
|---|---|
| `local` | Testing, no-API-key environments |
| `openai` | Production AI analysis (GPT models) |
| `claude` | Production AI analysis (Anthropic) |
| `gemini` | Production AI analysis (Google) |
| `ollama` | Air-gapped / self-hosted AI |
| `copilot` | Microsoft ecosystem |
| `grok` | xAI ecosystem |
| `mistral` | Mistral ecosystem |

All AI providers (except `local`) prompt for an API key and model. The wizard stores credentials in an `IntelligenceProvider` DB record — no env vars needed.

**Tip:** Start with `local` to verify the pipeline works end-to-end, then switch to a real
provider later by re-running the wizard.

### Step 7: Configure notifications

Same as Use Case 1, Step 5.

### Step 8: Review, apply, and verify

```bash
# Review the pipeline
uv run python manage.py run_pipeline --definition full --dry-run

# Test with a sample alert payload
uv run python manage.py run_pipeline --definition full --sample
```

The `--sample` flag sends a test alert through the pipeline so you can verify every stage
works without needing a real webhook.

### Step 9: Point your monitoring tool at the webhook endpoint

Start the Django server:

```bash
uv run python manage.py runserver 0.0.0.0:8000
```

Configure your monitoring tool to send webhooks to:

```
http://<your-server>:8000/api/alerts/webhook/
```

The alert ingestion endpoint auto-detects the source driver from the payload format. Alerts
from Grafana, AlertManager, PagerDuty, and other supported tools are parsed automatically.

---

## Use Case 3: Central Pipeline Hub

**Scenario:** This server acts as a central alert aggregation point. It receives webhooks
from multiple monitored servers, runs AI analysis, and dispatches notifications. It does
**not** run local health checks.

### Step 1: Run the setup wizard

```bash
uv run python manage.py setup_instance
```

### Step 2: Configure

1. Select **External webhooks** as alert source
2. Select **Alert → Intelligence → Notify** (ai-analyzed) as preset
3. Configure alert drivers (enable all you expect to receive)
4. Configure intelligence provider (recommend `openai` or `claude` for production)
5. Configure notification channels

### Step 3: Verify and run

```bash
# Dry run
uv run python manage.py run_pipeline --definition ai-analyzed --dry-run

# Test with sample payload
uv run python manage.py run_pipeline --definition ai-analyzed --sample

# Test with a specific source format
uv run python manage.py run_pipeline --definition ai-analyzed --sample --source grafana
```

### Step 4: Deploy

Start the server and point all your monitoring tools at the webhook endpoint:

```
http://<your-server>:8000/api/alerts/webhook/
```

---

## Running Pipelines from JSON Files

Instead of the setup wizard, you can run pipelines directly from JSON configuration files.
Sample pipelines are included in the project:

```bash
# List available sample pipelines
ls apps/orchestration/management/commands/pipelines/
```

| File | Description |
|---|---|
| `local-monitor.json` | Ingest → Checkers (cpu, memory, disk) → Intelligence (local) → Notify (generic) |
| `pagerduty-alert.json` | Ingest (PagerDuty) → Intelligence (OpenAI) → Notify (PagerDuty) |
| `pipeline-manager.json` | Ingest → Intelligence (local) → Notify (generic) |

Run a pipeline from a JSON file:

```bash
# Dry run to see the node chain
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json --dry-run

# Run it
uv run python manage.py run_pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json
```

### Writing your own pipeline JSON

A pipeline definition is a JSON file with this structure:

```json
{
  "version": "1.0",
  "description": "My custom pipeline",
  "defaults": {
    "max_retries": 3,
    "timeout_seconds": 300
  },
  "nodes": [
    {
      "id": "check_health",
      "type": "context",
      "config": {
        "checker_names": ["cpu", "memory", "disk"]
      },
      "next": "notify_ops"
    },
    {
      "id": "notify_ops",
      "type": "notify",
      "config": {
        "drivers": ["slack", "email"]
      }
    }
  ]
}
```

**Node fields:**

| Field | Required | Description |
|---|---|---|
| `id` | Yes | Unique identifier for this node |
| `type` | Yes | Node type (see table below) |
| `config` | Yes | Node-specific configuration |
| `next` | No | ID of the next node in the chain |
| `required` | No | If `false`, pipeline continues even if this node fails (default: `true`) |

**Available node types:**

| Type | Purpose | Key config |
|---|---|---|
| `ingest` | Parse incoming alert webhooks | `source_hint` (optional driver name) |
| `context` | Run health checkers | `checker_names` (list; defaults to all enabled) |
| `intelligence` | AI analysis | `provider` (required), `provider_config` (optional) |
| `notify` | Send notifications | `drivers` (list of driver names) |
| `transform` | Transform data between nodes | `source_node`, `extract`, `filter_priority`, `mapping` |

---

## Monitoring Your Pipelines

### View recent pipeline runs

```bash
uv run python manage.py monitor_pipeline
```

This shows the 10 most recent runs with their status, duration, and stage progress.

### Filter by status

```bash
# Show only failed runs
uv run python manage.py monitor_pipeline --status failed

# Show more results
uv run python manage.py monitor_pipeline --limit 50
```

### Inspect a specific run

```bash
uv run python manage.py monitor_pipeline --run-id <run-id>
```

This shows full details for a single run including each stage execution, timing, and any
errors.

### Pipeline statuses

| Status | Meaning |
|---|---|
| `pending` | Pipeline created but not started |
| `ingested` | Alert ingestion completed |
| `checked` | Health checks completed |
| `analyzed` | Intelligence analysis completed |
| `notified` | Notifications sent — pipeline complete |
| `failed` | A stage failed after retries exhausted |
| `retrying` | A stage is being retried |
| `skipped` | Pipeline was skipped (e.g., duplicate alert) |

---

## Reference

### Pipeline presets

| Name | Flow | Source | Description |
|---|---|---|---|
| `direct` | Alert → Notify | External | Forward alerts directly to notifications |
| `health-checked` | Alert → Checkers → Notify | External | Enrich alerts with health check context |
| `ai-analyzed` | Alert → Intelligence → Notify | External | Analyze alerts with AI before notifying |
| `full` | Alert → Checkers → Intelligence → Notify | External | Full processing pipeline |
| `local-monitor` | Checkers → Notify | Local | Local health monitoring via cron |
| `local-smart` | Checkers → Intelligence → Notify | Local | Local monitoring with AI analysis |

### Health checkers

| Checker | What it monitors | Platform |
|---|---|---|
| `cpu` | CPU usage percentage (multi-sample averaging) | All |
| `memory` | RAM usage and availability | All |
| `disk` | Disk usage for specified mount points | All (auto-detects platform) |
| `disk_common` | Common disk operations | All |
| `disk_linux` | Linux-specific disk metrics | Linux |
| `disk_macos` | macOS-specific disk metrics | macOS |
| `network` | Ping connectivity to specified hosts | All |
| `process` | Whether specified processes are running | All |

### Intelligence providers

Providers are configured via the `setup_instance` wizard or Django Admin (`IntelligenceProvider` model). API keys are stored in the DB, not environment variables.

| Provider | Notes |
|---|---|
| `local` | Rule-based, no API calls. Always available as fallback. |
| `openai` | GPT models (default: gpt-4o-mini) |
| `claude` | Anthropic Claude models (default: claude-sonnet-4-20250514) |
| `gemini` | Google Gemini models (default: gemini-2.0-flash) |
| `ollama` | Self-hosted via local Ollama server (default: llama3.1) |
| `copilot` | GitHub Copilot (default: gpt-4o) |
| `grok` | xAI Grok (default: grok-3-mini) |
| `mistral` | Mistral AI (default: mistral-small-latest) |

### Notification drivers

| Driver | Required config | Notes |
|---|---|---|
| `slack` | `webhook_url` | Slack incoming webhook |
| `email` | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_from`, `smtp_to` | SMTP email delivery |
| `pagerduty` | `routing_key` | PagerDuty Events API v2 |
| `generic` | `endpoint_url` | HTTP POST to any URL |

### Alert drivers

| Driver | Source tool |
|---|---|
| `alertmanager` | Prometheus AlertManager |
| `grafana` | Grafana Alerting |
| `pagerduty` | PagerDuty webhooks |
| `datadog` | Datadog webhooks |
| `newrelic` | New Relic webhooks |
| `opsgenie` | OpsGenie webhooks |
| `zabbix` | Zabbix webhooks |
| `generic` | Any JSON payload (fallback) |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | `3` | Max retry attempts per stage |
| `ORCHESTRATION_BACKOFF_FACTOR` | `2.0` | Exponential backoff multiplier |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | `1` | Continue pipeline if AI fails |

---

## Troubleshooting

### "No notification channels found"

The notify node couldn't find active `NotificationChannel` records matching the configured
drivers. Fix:

```bash
# Check what channels exist
uv run python manage.py shell -c "from apps.notify.models import NotificationChannel; print(list(NotificationChannel.objects.filter(is_active=True).values_list('name', 'driver')))"

# Re-run the wizard to create channels
uv run python manage.py setup_instance
```

### Checker doesn't run

If a checker doesn't run, verify it's included in your pipeline definition's `checker_names` config. If `checker_names` is omitted, all registered checkers run by default.

### Intelligence provider times out

The intelligence node has a 1-second timeout for provider responses. If your provider is
slow:

1. Check your API key is valid and has quota
2. Try the `local` provider to confirm the pipeline works without AI
3. Check network connectivity to the provider's API

### Pipeline fails at ingest with no payload

When using `--definition`, you may need to provide a payload:

```bash
# For local monitoring pipelines (no ingest node), no payload needed:
uv run python manage.py run_pipeline --definition local-monitor

# For webhook pipelines, provide a payload:
uv run python manage.py run_pipeline --definition full --sample
uv run python manage.py run_pipeline --definition full --file alert.json
```

### Re-running the setup wizard

The wizard detects existing configurations. When you re-run it, it shows the current pipeline
details so you can make an informed decision:

```
--- Existing pipeline: "local-smart" ---
  Flow: check_health → analyze_incident → notify_channels
  Checkers: cpu, memory, disk
  Intelligence: local
  Notify drivers: slack
  Channels:
    - ops-slack (slack)
  Created: 2026-02-28 14:30

? What would you like to do?
  1) Reconfigure — Replace existing pipeline and channels
  2) Add another — Create additional pipeline alongside existing
  3) Cancel
```

- **Reconfigure** — Deactivates existing pipeline and channels, creates new ones
- **Add another** — Creates an additional pipeline alongside the existing one
- **Cancel** — Exit without changes

```bash
uv run python manage.py setup_instance
```

### Viewing pipeline errors

```bash
# Show failed runs
uv run python manage.py monitor_pipeline --status failed

# Inspect a specific run for error details
uv run python manage.py monitor_pipeline --run-id <run-id>
```

Each `StageExecution` record stores the error type, message, and stack trace for debugging.