# Setup Instance — Pipeline Configuration Wizard

**Date:** 2026-02-19
**Status:** Approved

## Overview

A Django management command (`manage.py setup_instance`) that guides users through
configuring their server-maintanence instance after the basic install (`bin/install.sh`)
is complete. The wizard selects a pipeline preset, configures drivers per stage, collects
credentials, writes `.env` updates, and creates `PipelineDefinition` + `NotificationChannel`
records in the database.

## Presets

Four pipeline presets covering all meaningful stage combinations:

| # | Name | Stages | PipelineDefinition Nodes | Use Case |
|---|------|--------|--------------------------|----------|
| 1 | `direct` | Alert → Notify | `ingest → notify` | Simple webhook relay, low-volume alerts |
| 2 | `health-checked` | Alert → Checkers → Notify | `ingest → context → notify` | Server monitoring without AI |
| 3 | `ai-analyzed` | Alert → Intelligence → Notify | `ingest → intelligence → notify` | Alert triage with AI recommendations |
| 4 | `full` | Alert → Checkers → Intelligence → Notify | `ingest → context → intelligence → notify` | Comprehensive monitoring and incident response |

## User Flow

```
$ uv run python manage.py setup_instance

╔══════════════════════════════════════════════════╗
║     Server Maintenance — Instance Setup          ║
╚══════════════════════════════════════════════════╝

? How will you use this instance?

  1) Alert → Notify              (Direct forwarding)
  2) Alert → Checkers → Notify   (Health-checked alerts)
  3) Alert → Intelligence → Notify  (AI-analyzed alerts)
  4) Alert → Checkers → Intelligence → Notify  (Full pipeline)

> 4

--- Stage: Alerts ---
? Which alert drivers do you want to enable?
  (multi-select from 8 drivers)

--- Stage: Checkers ---
? Which health checkers do you want to enable?
  (multi-select from 8 checkers, per-checker config where needed)

--- Stage: Intelligence ---
? Which AI provider do you want to use?
  (single-select: local or openai, collect credentials)

--- Stage: Notify ---
? Which notification channels do you want to configure?
  (multi-select, then per-driver credential collection)

--- Summary ---
(review all selections)

? Apply this configuration? [Y/n]

✓ Configuration complete!
```

## Per-Stage Driver Configuration

### Alerts

Multi-select from the 8 drivers: `alertmanager`, `grafana`, `pagerduty`, `datadog`,
`newrelic`, `opsgenie`, `zabbix`, `generic`.

Controls which webhook endpoints are documented in the summary. Drivers are auto-detected
at runtime, so this is informational. Sets `ALERTS_ENABLED_DRIVERS` in `.env` as a
comma-separated list.

### Checkers

Multi-select from: `cpu`, `memory`, `disk`, `disk_macos`, `disk_linux`, `disk_common`,
`network`, `process`.

Writes `CHECKERS_SKIP` as the inverse (skip what's not selected). Per-checker prompts:

- `disk`: paths to monitor (default: `/`)
- `network`: hosts to ping (default: `8.8.8.8,1.1.1.1`)
- `process`: process names to watch

### Intelligence

Single-select between providers:

- `local`: Rule-based analysis, no config needed
- `openai`: Collect `OPENAI_API_KEY`, `OPENAI_MODEL` (default: `gpt-4o-mini`)

Sets `INTELLIGENCE_PROVIDER` in `.env`.

### Notify

Multi-select from: `email`, `slack`, `pagerduty`, `generic`. Per-driver prompts:

- `email`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TO`
- `slack`: `webhook_url` (stored in `NotificationChannel.config`)
- `pagerduty`: `routing_key` (stored in `NotificationChannel.config`)
- `generic`: `endpoint_url`, optional `headers` (stored in `NotificationChannel.config`)

Each driver creates a `NotificationChannel` record in the DB.

## Data Storage

### .env Updates

The wizard reads the existing `.env`, updates/adds only relevant keys, and preserves
everything else. Example additions for full pipeline + OpenAI + Slack:

```bash
# --- setup_instance: Generated 2026-02-19 ---
CHECKERS_SKIP=network,process,disk_macos,disk_linux,disk_common
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
INTELLIGENCE_PROVIDER=openai
```

### Database Records

- **PipelineDefinition**: Created with the selected preset's node configuration.
  Tagged with `"setup_wizard"` in `tags` for re-run detection.
- **NotificationChannel**: One per selected notify driver. Description prefixed
  with `[setup_wizard]` for re-run detection.

Notification channel credentials (webhook URLs, API keys) go into
`NotificationChannel.config` JSON field, not `.env`.

## Re-run Behavior

When existing wizard-created configuration is detected:

```
? Existing pipeline "full-pipeline" found. What would you like to do?
  1) Reconfigure — Replace existing pipeline and channels
  2) Add another — Create an additional pipeline alongside existing
  3) Cancel
```

- **Reconfigure**: Deactivates old PipelineDefinition and wizard-created
  NotificationChannels, creates new ones. Updates `.env`.
- **Add another**: Creates new PipelineDefinition with incremented name
  (e.g., `full-pipeline-2`). Adds new channels without touching existing ones.

## Code Structure

```
apps/orchestration/management/commands/setup_instance.py   # The command
apps/orchestration/_tests/test_setup_instance.py           # Tests
```

### Command internals

- `Command.handle()` — orchestrates the flow
- Step functions: `_select_preset()`, `_configure_alerts()`, `_configure_checkers()`,
  `_configure_intelligence()`, `_configure_notify()`, `_show_summary()`, `_apply_config()`
- Input helpers: `_prompt_choice()`, `_prompt_multi()`, `_prompt_input()`
- Config helpers: `_detect_existing()`, `_write_env()`, `_create_pipeline_definition()`,
  `_create_notification_channels()`

All step functions take/return data (pure-ish), making them testable without interactive input.

## Testing Strategy

- **Unit tests per step function**: Mock `input()` / `self.stdout`, verify data structures
- **Integration test**: Mock all prompts, run full `handle()`, verify DB records + `.env`
- **Re-run test**: Create existing records, run wizard with "reconfigure", verify old
  records deactivated and new ones created
- **Edge cases**: Cancel mid-flow, empty selections, invalid input format validation