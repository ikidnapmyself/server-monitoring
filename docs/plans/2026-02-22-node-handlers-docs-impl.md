# Node Handlers Documentation Update — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update all project documentation to reflect real node handler implementations, add a tutorial for building custom pipelines, fix outdated references and a config key bug.

**Architecture:** This is a documentation-only change across 7 files. One JSON file has a config key bug fix (`"include"` → `"checker_names"`). No Python code changes. Each task modifies one file and commits independently.

**Tech Stack:** Markdown, JSON

---

### Task 1: Fix local-monitor.json config key bug

**Files:**
- Modify: `apps/orchestration/management/commands/pipelines/local-monitor.json:19`
- Test: `apps/orchestration/_tests/test_run_pipeline_command.py` (existing test validates this file)

**Step 1: Fix the config key**

Change line 19 from:
```json
"include": ["cpu", "memory", "disk"]
```
to:
```json
"checker_names": ["cpu", "memory", "disk"]
```

The real `ContextNodeHandler.execute()` reads `config.get("checker_names")` (see `apps/orchestration/nodes/context.py:23`). The old `"include"` key is silently ignored, causing the node to run ALL enabled checkers instead of just the three specified.

**Step 2: Run the existing test to verify**

Run: `uv run pytest apps/orchestration/_tests/test_run_pipeline_command.py::TestSamplePipelineDefinitions::test_local_monitor_json_is_valid -v`
Expected: PASS (the test validates structure, not config key names)

**Step 3: Run the full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass

**Step 4: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/local-monitor.json
git commit -m "fix: correct config key in local-monitor.json (include → checker_names)"
```

---

### Task 2: Update Architecture.md — definition-based pipeline section

**Files:**
- Modify: `docs/Architecture.md:142-176`

**Step 1: Replace the definition-based pipeline section**

Find the section starting at line 142 (`### Definition-Based Pipeline`) through line 176 (end of the example JSON block). Replace it with the content below. Keep everything else in the file unchanged.

Replace lines 142-176 with:

~~~markdown
### Definition-Based Pipeline

**Location:** `apps/orchestration/definition_orchestrator.py`

Dynamic stages configured via JSON stored in `PipelineDefinition` model. Supports any combination and ordering of node types.

- **Endpoints:** `POST /orchestration/definitions/<name>/execute/`
- **CLI:** `python manage.py run_pipeline --definition <name>` or `--config path/to/file.json`
- **Celery support:** Not yet (sync only)
- **Resume:** Not yet

**Available node types:**

| Type | Handler | Purpose | Config Keys |
|------|---------|---------|-------------|
| `ingest` | IngestNodeHandler | Parse alert webhooks, create Incident + Alert records | `driver` (optional) |
| `context` | ContextNodeHandler | Run real system health checkers (CPU, memory, disk, etc.) | `checker_names` (list, optional — defaults to all enabled) |
| `intelligence` | IntelligenceNodeHandler | AI analysis via provider pattern (local or OpenAI) | `provider` (required), `provider_config` (optional) |
| `notify` | NotifyNodeHandler | Send notifications via DB-configured channels | `drivers` (list) or `driver` (string) |
| `transform` | TransformNodeHandler | Extract, filter, or map data between nodes | `source_node` (required), `extract`, `mapping`, `filter_priority` |

**Node output chaining:** Each node's output is stored in `NodeContext.previous_outputs[node_id]` and available to all downstream nodes. For example, the `notify` node reads checker results from previous context node output to build notification messages with appropriate severity (critical/warning/info).

**Example definition (local health check → notify):**

```json
{
  "version": "1.0",
  "nodes": [
    {"id": "check_health", "type": "context", "config": {"checker_names": ["cpu", "memory", "disk"]}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}}
  ]
}
```

Definitions can be created via:
- **Django Admin:** `/admin/orchestration/pipelinedefinition/`
- **Setup wizard:** `python manage.py setup_instance`
~~~

**Step 2: Verify the file renders correctly**

Read the file and confirm the section looks correct, the table renders, and the surrounding content (Comparison table on line 177+) is intact.

**Step 3: Commit**

```bash
git add docs/Architecture.md
git commit -m "docs: update Architecture.md with real node handler details"
```

---

### Task 3: Update orchestration README — add node handlers reference, tutorial, and troubleshooting

**Files:**
- Modify: `apps/orchestration/README.md`

This is the largest task. Add new sections after the existing "CLI Reference" section (after line 256, before `### monitor_pipeline`). Also fix the test path reference on line 388.

**Step 1: Fix test path on line 388**

Change:
```
uv run pytest apps/orchestration/tests.py -v
```
to:
```
uv run pytest apps/orchestration/_tests/ -v
```

**Step 2: Add new sections before `### monitor_pipeline` (insert after line 256)**

Insert the following content after line 256 (after the flag reference table, before `---`):

~~~markdown

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
~~~

**Step 3: Verify rendering**

Read the file and confirm all new sections are properly placed between the `run_pipeline` flag reference and the `monitor_pipeline` section.

**Step 4: Commit**

```bash
git add apps/orchestration/README.md
git commit -m "docs: add node handler reference, tutorial, and troubleshooting to orchestration README"
```

---

### Task 4: Update pipelines README — sample vs wizard comparison

**Files:**
- Modify: `apps/orchestration/management/commands/pipelines/README.md`

**Step 1: Replace the "Creating Custom Pipelines" section and add new content**

Find the section `## Creating Custom Pipelines` (line 39) and replace everything from line 39 through the end of the `## Notification Drivers` section (line 163, end of file) with the updated content below:

~~~markdown
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
~~~

**Step 2: Verify rendering**

Read the file and confirm all sections are properly structured.

**Step 3: Commit**

```bash
git add apps/orchestration/management/commands/pipelines/README.md
git commit -m "docs: add sample vs wizard comparison and per-sample annotations to pipelines README"
```

---

### Task 5: Update orchestration agents.md — add node handler contracts

**Files:**
- Modify: `apps/orchestration/agents.md`

**Step 1: Add node handler contracts after the "Key modules" section (after line 39)**

Insert after line 39 (after the key modules list, before `## App layout rules`):

~~~markdown

## Node handler contracts

Each definition-based pipeline node has a handler in `apps/orchestration/nodes/`. Below are the input/output contracts:

| Node Type | Config Required | Config Optional | Output Keys | Error Behavior |
|-----------|----------------|-----------------|-------------|----------------|
| `ingest` | — | `driver` | `alerts_created`, `incident_id`, `severity` | Fails on invalid payload |
| `context` | — | `checker_names` (list) | `checks_run`, `checks_passed`, `checks_failed`, `results` | Individual checker failures → `"unknown"` status, node continues |
| `intelligence` | `provider` | `provider_config` | `provider`, `recommendations`, `summary` | Fails on exception; use `"required": false` to make optional |
| `notify` | `drivers` (list) or `driver` (string) | — | `channels_attempted`, `channels_succeeded`, `deliveries` | Partial failure OK; errors only if ALL channels fail |
| `transform` | `source_node` | `extract`, `mapping`, `filter_priority` | `transformed`, `source_node` | Fails on exception |

**Output chaining:** Each node's output is stored in `ctx.previous_outputs[node_id]` and available to all downstream nodes. The `notify` node reads checker/intelligence outputs to build smart notification messages with derived severity.

**Key files:**
- `apps/orchestration/nodes/base.py` — `NodeContext`, `NodeResult`, `BaseNodeHandler`
- `apps/orchestration/nodes/context.py` — runs `CHECKER_REGISTRY` checkers
- `apps/orchestration/nodes/notify.py` — queries `NotificationChannel` DB records, uses `DRIVER_REGISTRY`
- `apps/orchestration/nodes/intelligence.py` — calls provider with timeout
- `apps/orchestration/nodes/ingest.py` — wraps `AlertOrchestrator`
- `apps/orchestration/nodes/transform.py` — extract/filter/map operations
~~~

**Step 2: Commit**

```bash
git add apps/orchestration/agents.md
git commit -m "docs: add node handler contracts to orchestration agents.md"
```

---

### Task 6: Fix notify README — wrong skip example

**Files:**
- Modify: `apps/notify/README.md:200-208`

**Step 1: Fix the skip example**

Find lines 191-208 (the section with `NOTIFY_SKIP=network,process`). The text says "Skip network and process drivers" but `network` and `process` are checker names, not notification driver names. The valid notify drivers are: `slack`, `email`, `pagerduty`, `generic`.

Replace:
```bash
# Skip network and process drivers
export NOTIFY_SKIP=network,process

# Then run checks - network and process will be skipped
uv run python manage.py check_and_alert
```

With:
```bash
# Skip specific notification drivers
export NOTIFY_SKIP=email,pagerduty

# Then run pipeline - email and pagerduty notifications will be skipped
uv run python manage.py run_pipeline --sample
```

Also fix line 191-192, which says "every checker" but should say "every driver":

Replace:
```
If you want to disable *every* checker (common when using the app as a pipeline controller and you want
`alerts → checkers → intelligence` without notifications), set:
```

With:
```
If you want to disable *every* notification driver (common when using the app as a pipeline controller
without notifications), set:
```

**Step 2: Commit**

```bash
git add apps/notify/README.md
git commit -m "docs: fix NOTIFY_SKIP example to use driver names instead of checker names"
```

---

### Task 7: Mark design doc as completed

**Files:**
- Modify: `docs/plans/2026-02-22-real-node-handlers-design.md:4`

**Step 1: Update status line**

Change line 4 from:
```
**Status:** Approved
```
to:
```
**Status:** Completed (implemented 2026-02-22)
```

**Step 2: Commit**

```bash
git add docs/plans/2026-02-22-real-node-handlers-design.md
git commit -m "docs: mark real node handlers design as completed"
```