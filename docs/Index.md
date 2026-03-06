---
title: Home
layout: default
nav_order: 1
permalink: /
---

# Project Wiki

## Table of Contents

- [System Overview](#system-overview)
- [Alerts App](#alerts-app)
- [Checkers App](#checkers-app)
- [Intelligence App](#intelligence-app)
- [Notify App](#notify-app)
- [Orchestration App](#orchestration-app)
- [Data Object Reference](#data-object-reference)

---

## System Overview

### Architecture

This is a Django-based server monitoring and alerting system built around a strict 4-stage orchestration pipeline. The orchestrator is the single point of control — stages never call downstream stages directly.

```
                Webhooks (8 drivers)
                          |
                          v
              +----------------------+
              |   alerts.ingest()    |  Stage 1: Parse webhook, create Alert + Incident
              +----------------------+
                          |
                          v
              +----------------------+
              |   checkers.run()     |  Stage 2: Run health checks (CPU, memory, disk, etc.)
              +----------------------+
                          |
                          v
              +----------------------+
              | intelligence.analyze |  Stage 3: AI analysis via provider pattern
              +----------------------+
                          |
                          v
              +----------------------+
              |   notify.dispatch()  |  Stage 4: Send notifications (Email, Slack, PagerDuty, etc.)
              +----------------------+
```

### Who Is This For

- **Server operators** who want automated health monitoring with alerts — no external tools needed, just cron and this app.
- **Platform teams** who already use Grafana, PagerDuty, Datadog, or other monitoring tools and want to enrich alerts with AI analysis before routing notifications.
- **Small teams** who need a central alert hub that aggregates webhooks from multiple sources, analyzes them, and dispatches to Slack, email, or PagerDuty.

Pick a pipeline preset that matches your situation:

| I want to... | Preset | Alert source | Pipeline |
|---|---|---|---|
| Monitor this server (basic) | `local-monitor` | Local crontab | Checkers -> Notify |
| Monitor this server (with AI) | `local-smart` | Local crontab | Checkers -> Intelligence -> Notify |
| Forward alerts to notifications | `direct` | External webhooks | Alert -> Notify |
| Forward alerts with health context | `health-checked` | External webhooks | Alert -> Checkers -> Notify |
| Forward alerts with AI analysis | `ai-analyzed` | External webhooks | Alert -> Intelligence -> Notify |
| Full alert processing pipeline | `full` | External webhooks | Alert -> Checkers -> Intelligence -> Notify |

See the [Setup Guide](Setup-Guide) for step-by-step walkthroughs of each use case.

### Design Principles

- **One Orchestrator, One Trace**: Only `apps.orchestration` moves work between stages. Every run carries `trace_id` and `run_id` across all stages, logs, and DB records.
- **Stage Isolation**: Stages communicate through DTOs. A stage returns a result; the orchestrator passes it forward. Stages never import or call downstream stages.
- **Driver/Provider Pattern**: All integrations inherit from abstract base classes (`BaseDriver`, `BaseChecker`, `BaseProvider`, `BaseNotifyDriver`).
- **Retry with Backoff**: Per-stage retries with exponential backoff (2^attempt seconds). Failed pipelines can be resumed from the last successful stage.
- **Intelligence Fallback**: If AI analysis fails, the pipeline continues with a local fallback provider rather than failing entirely.
- **Skip Controls**: `CHECKERS_SKIP_ALL=1` or `CHECKERS_SKIP=cpu,memory` to disable specific stages or checkers.

### Key Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | 3 | Max retry attempts per stage |
| `ORCHESTRATION_BACKOFF_FACTOR` | 2.0 | Exponential backoff multiplier |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | True | Use local fallback if AI fails |
| `ORCHESTRATION_METRICS_BACKEND` | "logging" | Signal backend (logging or statsd) |
| `CHECKERS_SKIP_ALL` | 0 | Disable all checkers |
| `CHECKERS_SKIP` | "" | Comma-separated checkers to skip |
| `ENABLE_CELERY_ORCHESTRATION` | 0 | Enable async pipeline via Celery |

---

## Alerts App

**Purpose**: Webhook ingestion from 8 monitoring platforms, alert deduplication, and incident lifecycle management.

**Location**: `apps/alerts/`

### Models

#### Alert

Represents a single alert received from an external source.

| Field | Type | Description |
|-------|------|-------------|
| `fingerprint` | CharField (indexed) | Unique identifier for deduplication (SHA256-based) |
| `source` | CharField (indexed) | Source system (e.g., "alertmanager", "grafana") |
| `name` | CharField | Alert name/title |
| `severity` | CharField (choices) | "critical", "warning", "info" |
| `status` | CharField (choices, indexed) | "firing", "resolved" |
| `description` | TextField | Detailed alert description |
| `labels` | JSONField | Key-value label pairs |
| `annotations` | JSONField | Additional metadata (runbook URLs, etc.) |
| `raw_payload` | JSONField | Original raw payload from source |
| `started_at` | DateTimeField (indexed) | When alert started |
| `ended_at` | DateTimeField (nullable) | When alert resolved (null if firing) |
| `received_at` | DateTimeField (auto_now_add, indexed) | When we received it |
| `updated_at` | DateTimeField (auto_now) | Last update time |
| `incident` | ForeignKey (nullable) | Link to parent Incident |

Properties: `is_firing`, `duration`

#### Incident

Groups related firing alerts and tracks lifecycle.

| Field | Type | Description |
|-------|------|-------------|
| `title` | CharField | Incident title |
| `status` | CharField (choices, indexed) | "open", "acknowledged", "resolved", "closed" |
| `severity` | CharField (choices, indexed) | "critical", "warning", "info" |
| `description` | TextField | Incident description |
| `summary` | TextField | Summary and resolution steps |
| `created_at` | DateTimeField (auto_now_add, indexed) | Creation time |
| `updated_at` | DateTimeField (auto_now) | Last update time |
| `acknowledged_at` | DateTimeField (nullable) | When acknowledged |
| `resolved_at` | DateTimeField (nullable) | When resolved |
| `closed_at` | DateTimeField (nullable) | When closed |
| `metadata` | JSONField | Custom metadata (notes, etc.) |

Methods: `acknowledge()`, `resolve()`, `close()`
Properties: `is_open`, `is_resolved`, `alert_count`, `firing_alert_count`

#### AlertHistory

Audit trail of state changes and events for alerts.

| Field | Type | Description |
|-------|------|-------------|
| `alert` | ForeignKey | Link to Alert |
| `event` | CharField | Event type ("created", "resolved", "escalated", etc.) |
| `old_status` | CharField | Previous status |
| `new_status` | CharField | New status |
| `details` | JSONField | Event-specific metadata |
| `created_at` | DateTimeField (auto_now_add, indexed) | Event time |

### DTOs

#### ParsedAlert (dataclass)

Standard alert format produced by all drivers.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fingerprint` | str | required | Unique alert identifier |
| `name` | str | required | Alert name |
| `status` | str | required | "firing" or "resolved" (auto-normalized) |
| `started_at` | datetime | required | When alert started |
| `severity` | str | "warning" | "critical", "warning", or "info" (auto-normalized) |
| `description` | str | "" | Alert description |
| `labels` | dict[str, str] | {} | Label pairs |
| `annotations` | dict[str, str] | {} | Additional metadata |
| `ended_at` | datetime or None | None | Resolution time |
| `raw_payload` | dict | {} | Original payload from source |

#### ParsedPayload (dataclass)

Result of parsing an incoming webhook payload.

| Field | Type | Description |
|-------|------|-------------|
| `alerts` | list[ParsedAlert] | List of parsed alerts |
| `source` | str | Source system name |
| `version` | str | Optional version string |
| `group_key` | str | Optional grouping key |
| `receiver` | str | Optional receiver name |
| `external_url` | str | Optional external URL |
| `raw_payload` | dict | Original raw payload |

#### ProcessingResult (dataclass)

Result of processing an incoming alert payload.

| Field | Type | Description |
|-------|------|-------------|
| `alerts_created` | int | Number of new alerts created |
| `alerts_updated` | int | Number of existing alerts updated |
| `alerts_resolved` | int | Number of alerts resolved |
| `incidents_created` | int | Number of new incidents created |
| `incidents_updated` | int | Number of incidents updated |
| `errors` | list[str] | Error messages if any |

Properties: `total_processed`, `has_errors`

### Drivers (8 webhook drivers)

All drivers inherit from `BaseAlertDriver` and implement `validate(payload)` and `parse(payload)`.

| Driver | Source | Validates By |
|--------|--------|-------------|
| `AlertManagerDriver` | Prometheus AlertManager | "alerts" + "status" + groupKey/receiver/groupLabels |
| `GrafanaDriver` | Grafana | orgId + state + title, or alerts + dashboardId |
| `PagerDutyDriver` | PagerDuty | V3 event.event_type or V2 messages array |
| `DatadogDriver` | Datadog | alert_id + alert_status + alert_type + alert_transition |
| `NewRelicDriver` | New Relic | condition_id + incident_id + policy_name, or workflow format |
| `OpsGenieDriver` | OpsGenie | alert + action fields, or integrationId |
| `ZabbixDriver` | Zabbix | event_id + trigger_id + trigger_name + trigger_severity |
| `GenericWebhookDriver` | Any | alerts list or name/title field (fallback driver) |

**Driver detection**: `detect_driver(payload)` tries specific drivers first, falls back to generic.

### Ingest Flow

Entry point: `AlertOrchestrator.process_webhook(payload, driver=None)`

1. **Driver Resolution** — Auto-detect or use specified driver
2. **Parse Payload** — Driver parses into `ParsedPayload`
3. **Atomic Processing** — For each alert:
   - Check if exists by (fingerprint, source)
   - Create or update alert, record `AlertHistory`
   - Auto-create/attach incident for firing alerts
4. **Auto-resolution** — Resolve incidents where all alerts are resolved

### Services

- **AlertOrchestrator** — Main ingestion orchestrator
- **IncidentManager** — Static methods for incident lifecycle (acknowledge, resolve, close, add_note)
- **AlertQueryService** — Convenience query methods (firing alerts, by severity, by source, recent)
- **CheckAlertBridge** — Bridge converting `CheckResult` objects into Alert records

### URL Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/alerts/webhook/` | Auto-detect driver |
| POST | `/alerts/webhook/<driver>/` | Force specific driver |
| GET | `/alerts/webhook/` | Health check |

---

## Checkers App

**Purpose**: Health checks for CPU, memory, disk, network, and process monitoring with an audit trail.

**Location**: `apps/checkers/`

### Models

#### CheckRun

Audit trail of standalone health check executions.

| Field | Type | Description |
|-------|------|-------------|
| `checker_name` | CharField (indexed) | Name of the checker run |
| `hostname` | CharField (indexed) | Host where check ran |
| `status` | CharField (choices, indexed) | "ok", "warning", "critical", "unknown" |
| `message` | TextField | Human-readable description |
| `metrics` | JSONField | Measured values (e.g., `{"cpu_percent": 45.2}`) |
| `error` | TextField | Error message if check failed |
| `warning_threshold` | FloatField (nullable) | Warning threshold used |
| `critical_threshold` | FloatField (nullable) | Critical threshold used |
| `alert` | ForeignKey (nullable) | Link to created Alert if triggered |
| `duration_ms` | FloatField | Execution time in milliseconds |
| `executed_at` | DateTimeField (auto_now_add, indexed) | When check ran |
| `trace_id` | CharField (indexed) | Correlation ID for pipeline tracing |

Properties: `is_ok`, `is_critical`, `has_issue`, `created_alert`

### DTOs

#### CheckStatus (Enum)

`OK`, `WARNING`, `CRITICAL`, `UNKNOWN`

#### CheckResult (dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `status` | CheckStatus | Result status level |
| `message` | str | Human-readable description |
| `metrics` | dict[str, Any] | Measured values |
| `checker_name` | str | Name of checker producing result |
| `error` | str or None | Error message if check failed |

Methods: `is_ok()`, `is_critical()`

### BaseChecker (Abstract)

| Attribute | Default | Description |
|-----------|---------|-------------|
| `name` | "base" | Unique identifier |
| `timeout` | 10.0 | Max seconds to wait |
| `warning_threshold` | 70.0 | Threshold for WARNING |
| `critical_threshold` | 90.0 | Threshold for CRITICAL |
| `enabled` | True | Whether enabled |

Abstract method: `check() -> CheckResult`
Public method: `run(trace_id="") -> CheckResult` — runs check, times it, creates `CheckRun` audit record.

### Checker Implementations

#### CPUChecker

| Config | Default | Description |
|--------|---------|-------------|
| `samples` | 5 | Number of samples to take |
| `sample_interval` | 1.0 | Seconds between samples |
| `per_cpu` | False | Report per-CPU or system-wide |

**Metrics**: `cpu_percent`, `cpu_min`, `cpu_max`, `samples`, `cpu_count`, `per_cpu_percent` (optional)

#### MemoryChecker

| Config | Default | Description |
|--------|---------|-------------|
| `include_swap` | False | Include swap memory in check |

**Metrics**: `memory_percent`, `memory_total_gb`, `memory_used_gb`, `memory_available_gb`, `swap_*` (optional)

#### DiskChecker

| Config | Default | Description |
|--------|---------|-------------|
| `paths` | ["/"] | Mount points to check |

**Thresholds**: warning=80.0, critical=95.0
**Metrics**: `disks` (dict per path with percent/total_gb/used_gb/free_gb), `worst_percent`, `worst_path`

#### NetworkChecker

| Config | Default | Description |
|--------|---------|-------------|
| `hosts` | ["8.8.8.8", "1.1.1.1"] | Hosts to ping |
| `ping_count` | 1 | Packets per ping |

**Thresholds**: Inverted logic — warning=70.0 (min % reachable for OK), critical=50.0
**Metrics**: `hosts` (dict per host with reachable/latency_ms), `reachable_count`, `total_hosts`, `reachable_percent`

#### ProcessChecker

| Config | Default | Description |
|--------|---------|-------------|
| `processes` | [] | Process names to check |

**Thresholds**: Inverted — warning=100.0 (all must run), critical=50.0
**Metrics**: `processes` (dict per process with running/pid/status/cpu_percent/memory_percent), `running_count`, `total_count`, `running_percent`

#### DiskCommonChecker

Cross-platform disk analysis (Unix-like). Scans `/var/log`, `~/.cache`, `/tmp`, `/var/tmp` for space hogs, old files, and large files.

**Thresholds**: warning=5000 MB, critical=20000 MB
**Metrics**: `space_hogs`, `old_files`, `large_files`, `total_recoverable_mb`, `recommendations`

#### DiskMacOSChecker

macOS-specific. Scans `~/Library/Caches`, Xcode DerivedData, `~/Downloads` (>30 days).

#### DiskLinuxChecker

Linux-specific. Scans `/var/cache/apt/archives`, `/var/log/journal`, `/var/lib/docker`, `/var/lib/snapd`.

### Registry and Skip Controls

```python
CHECKER_REGISTRY = {
    "cpu": CPUChecker,
    "memory": MemoryChecker,
    "disk": DiskChecker,
    "disk_common": DiskCommonChecker,
    "disk_linux": DiskLinuxChecker,
    "disk_macos": DiskMacOSChecker,
    "network": NetworkChecker,
    "process": ProcessChecker,
}
```

- `CHECKERS_SKIP_ALL=1` — skip all checkers
- `CHECKERS_SKIP=cpu,memory` — skip specific checkers
- `get_enabled_checkers()` — returns filtered registry

---

## Intelligence App

**Purpose**: AI-powered incident analysis via a pluggable provider pattern with 8 providers.

**Location**: `apps/intelligence/`

### Models

#### IntelligenceProvider

Database-driven provider configuration with single-active enforcement.

| Field | Type | Description |
|-------|------|-------------|
| `name` | CharField (unique) | Human-readable identifier (e.g., "production-claude") |
| `provider` | CharField (choices) | Driver type: openai, claude, gemini, copilot, grok, ollama, mistral |
| `config` | JSONField | Provider-specific credentials and settings |
| `is_active` | BooleanField | Only one can be active (enforced atomically in `save()`) |
| `description` | TextField | Config notes |
| `created_at` | DateTimeField | Creation time |
| `updated_at` | DateTimeField | Last update |

#### AnalysisRun

Audit trail for every intelligence analysis execution.

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | CharField (indexed) | Correlation ID across pipeline stages |
| `pipeline_run_id` | CharField (indexed) | Pipeline run reference |
| `provider` | CharField | Provider name used (e.g., "openai", "local") |
| `provider_config` | JSONField | Redacted provider config (sensitive fields replaced with "***") |
| `model_name` | CharField | Model identifier used |
| `status` | CharField (choices, indexed) | PENDING, RUNNING, SUCCEEDED, FAILED |
| `error_message` | TextField | Exception message if failed |
| `fallback_used` | BooleanField | Whether AI analysis failed and fallback was used |
| `incident` | ForeignKey (nullable) | Incident analyzed |
| `input_summary` | TextField | Summary of input provided |
| `checker_output_ref` | CharField | Reference to checker output |
| `recommendations` | JSONField | List of recommendation dicts |
| `recommendations_count` | PositiveIntegerField | Count of recommendations |
| `summary` | TextField | Analysis summary |
| `probable_cause` | TextField | Root cause identified |
| `confidence` | FloatField (nullable) | Confidence score (0.0 to 1.0) |
| `prompt_tokens` | PositiveIntegerField (nullable) | Input token count |
| `completion_tokens` | PositiveIntegerField (nullable) | Output token count |
| `total_tokens` | PositiveIntegerField (nullable) | Total token count |
| `created_at` | DateTimeField | Creation time |
| `started_at` | DateTimeField (nullable) | When analysis started |
| `completed_at` | DateTimeField (nullable) | When analysis completed |
| `duration_ms` | FloatField | Execution time in milliseconds |

Methods: `mark_started()`, `mark_succeeded()`, `mark_failed()`

### DTOs

#### Recommendation (dataclass)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | RecommendationType | required | memory, disk, cpu, process, network, general |
| `priority` | RecommendationPriority | required | low, medium, high, critical |
| `title` | str | required | Short recommendation title |
| `description` | str | required | Detailed description of issue |
| `details` | dict | {} | Additional structured data |
| `actions` | list[str] | [] | Suggested actions to resolve |
| `incident_id` | int or None | None | Related incident ID |

Methods: `to_dict()`

#### RecommendationType (Enum)

`MEMORY`, `DISK`, `CPU`, `PROCESS`, `NETWORK`, `GENERAL`

#### RecommendationPriority (Enum)

`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`

### Provider Hierarchy

```
BaseProvider (ABC)
  |
  +-- LocalRecommendationProvider    (no LLM, system analysis)
  |
  +-- BaseAIProvider (ABC)
        |
        +-- OpenAIRecommendationProvider     (gpt-4o-mini)
        +-- ClaudeRecommendationProvider     (claude-sonnet-4-20250514)
        +-- GeminiRecommendationProvider     (gemini-2.0-flash)
        +-- CopilotRecommendationProvider    (gpt-4o via GitHub Copilot)
        +-- GrokRecommendationProvider       (grok-3-mini via xAI)
        +-- OllamaRecommendationProvider     (llama3.1 local)
        +-- MistralRecommendationProvider    (mistral-small-latest)
```

#### BaseProvider

- Abstract method: `analyze(incident, analysis_type) -> list[Recommendation]`
- Public method: `run(incident, trace_id)` — wraps `analyze()` with `AnalysisRun` audit logging
- Redacts sensitive config fields (keys containing "key", "secret", "token", "password", "api")

#### BaseAIProvider

- Shared base for all LLM-backed providers
- Builds prompt from incident context (title, description, severity, alerts, metadata)
- Parses JSON response (including markdown-wrapped JSON) into `Recommendation` objects
- Returns fallback recommendation on API error
- Subclasses only implement `_call_api(prompt) -> str`

#### LocalRecommendationProvider

Built-in fallback — analyzes actual system state without LLM calls.

| Config | Default | Description |
|--------|---------|-------------|
| `top_n_processes` | 10 | Top memory/CPU processes to report |
| `large_file_threshold_mb` | 100.0 | Minimum file size to flag |
| `old_file_days` | 30 | Age threshold for old files |
| `scan_paths` | ["/var/log", "/tmp", ...] | Directories to scan |

Auto-detects incident type from keywords in title/description:
- Memory keywords: "memory", "ram", "oom", "swap"
- Disk keywords: "disk", "storage", "space", "inode"
- CPU keywords: "cpu", "load", "processor"

### Provider Registry

```python
PROVIDERS = {
    "local": LocalRecommendationProvider,
    "openai": OpenAIRecommendationProvider,
    "claude": ClaudeRecommendationProvider,
    "gemini": GeminiRecommendationProvider,
    "copilot": CopilotRecommendationProvider,
    "grok": GrokRecommendationProvider,
    "ollama": OllamaRecommendationProvider,
    "mistral": MistralRecommendationProvider,
}
```

- `get_provider(name)` — instantiates by name
- `get_active_provider()` — queries `IntelligenceProvider` DB model for active provider, falls back to local

### URL Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/intelligence/health/` | Health check |
| GET | `/intelligence/providers/` | List available providers |
| GET/POST | `/intelligence/recommendations/` | Get recommendations |
| GET | `/intelligence/memory/` | Memory-specific analysis |
| GET | `/intelligence/disk/` | Disk-specific analysis |

---

## Notify App

**Purpose**: Notification delivery to 4 channels with templating support.

**Location**: `apps/notify/`

### Models

#### NotificationChannel

Persistent, named channel configuration.

| Field | Type | Description |
|-------|------|-------------|
| `name` | CharField (unique) | Unique channel identifier (e.g., "ops-slack") |
| `driver` | CharField (indexed) | Driver type: email, slack, pagerduty, generic |
| `config` | JSONField | Driver-specific configuration |
| `is_active` | BooleanField (indexed) | Enable/disable channel |
| `description` | TextField | Channel purpose description |
| `created_at` | DateTimeField | Creation time |
| `updated_at` | DateTimeField | Last update |

### DTOs

#### NotificationMessage (dataclass)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | str | required | Notification title |
| `message` | str | required | Notification body |
| `severity` | str | required | "critical", "warning", "info", "success" (auto-normalized) |
| `channel` | str | "default" | Routing/destination identifier |
| `tags` | dict[str, str] | {} | Key-value tags |
| `context` | dict[str, Any] | {} | Additional context data |

### BaseNotifyDriver (Abstract)

All drivers implement:
- `validate_config(config) -> bool` — Validate driver configuration
- `send(message, config) -> dict` — Send notification

**Standard return format** (all drivers):
```python
{"success": bool, "message_id": str | None, "error": str, "metadata": dict}
```

**Built-in constants**: `SEVERITY_COLORS`, `SEVERITY_EMOJIS`, `PRIORITY_MAP`

**Template helpers**: `_render_message_templates()`, `_compose_incident_details()`, `_prepare_notification()`

### Driver Implementations

#### EmailNotifyDriver

| Config Key | Required | Default | Description |
|------------|----------|---------|-------------|
| `smtp_host` | yes | - | SMTP server hostname |
| `from_address` | yes | - | Sender email address |
| `smtp_port` | no | 587 | SMTP port |
| `use_tls` | no | True | Enable TLS |
| `username` | no | - | SMTP auth username |
| `password` | no | - | SMTP auth password |
| `to_addresses` | no | - | Recipient list |
| `timeout` | no | 30 | Connection timeout |

Creates MIME multipart email with text, optional HTML, and JSON incident payload.

#### SlackNotifyDriver

| Config Key | Required | Default | Description |
|------------|----------|---------|-------------|
| `webhook_url` | yes | - | Slack webhook URL (must start with `https://hooks.slack.com/`) |
| `channel` | no | - | Override channel |
| `username` | no | - | Bot username |
| `icon_emoji` | no | - | Bot icon emoji |
| `timeout` | no | 30 | Request timeout |

Supports Slack Block Kit JSON format and plain text.

#### PagerDutyNotifyDriver

| Config Key | Required | Default | Description |
|------------|----------|---------|-------------|
| `integration_key` | yes | - | PagerDuty integration key (min 20 chars) |
| `event_action` | no | "trigger" | Event action type |
| `dedup_key` | no | - | Deduplication key |
| `client` | no | - | Client name |
| `client_url` | no | - | Client URL |
| `timeout` | no | 30 | Request timeout |

Additional methods: `acknowledge(dedup_key, config)`, `resolve(dedup_key, config)`

#### GenericNotifyDriver

| Config Key | Required | Default | Description |
|------------|----------|---------|-------------|
| `endpoint` or `webhook_url` | yes | - | HTTP endpoint |
| `method` | no | "POST" | HTTP method |
| `headers` | no | {} | Custom headers |
| `timeout` | no | 30 | Request timeout |
| `disabled` | no | False | Disable sending |

Supports POST, PUT, PATCH, GET. Disabled mode returns success without sending.

### Dispatch Flow

**Single notification**: `POST /notify/send/` or `POST /notify/send/<driver>/`
1. `NotifySelector.resolve()` determines provider/config/channel
2. Build `NotificationMessage` from payload
3. Validate config, send via driver
4. Return result with message_id/metadata

**Batch**: `POST /notify/batch/`
- Processes `notifications` array, returns aggregated results

**Channel selection priority**:
1. Provider arg matches active `NotificationChannel.name` -> use that channel
2. No provider arg -> use first active channel
3. Otherwise -> treat as driver key, use payload config

### Templating

`NotificationTemplatingService` supports:
- Inline Jinja2 templates (fallback to Python format)
- File references: `"file:slack_text.j2"` or `{"type": "file", "template": "slack_text.j2"}`
- Default template search: `<driver>_text.j2`, `<driver>_payload.j2`, `<driver>.j2`

Template context includes: `title`, `message`, `severity`, `channel`, `tags`, `context`, `incident` (metrics, summaries, recommendations).

### URL Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/notify/send/` | Send single notification (auto-detect driver) |
| POST | `/notify/send/<driver>/` | Send with specific driver |
| POST | `/notify/batch/` | Send batch notifications |
| GET | `/notify/drivers/` | List available drivers |
| GET | `/notify/drivers/<driver>/` | Driver config requirements |

---

## Orchestration App

**Purpose**: Pipeline state machine, retry logic, stage execution tracking, and monitoring signals.

**Location**: `apps/orchestration/`

### Models

#### PipelineRun

Main pipeline execution record.

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | CharField (indexed) | Correlation ID across all stages |
| `run_id` | CharField (unique, indexed) | Unique ID for this pipeline run |
| `status` | CharField (choices) | PENDING, INGESTED, CHECKED, ANALYZED, NOTIFIED, FAILED, RETRYING, SKIPPED |
| `current_stage` | CharField (nullable) | Currently/last executing stage |
| `incident` | ForeignKey (nullable) | Associated incident |
| `source` | CharField | Source system (grafana, alertmanager, etc.) |
| `alert_fingerprint` | CharField | Alert fingerprint for dedup |
| `environment` | CharField | Deployment environment (default: "production") |
| `normalized_payload_ref` | CharField | Reference to normalized inbound payload |
| `checker_output_ref` | CharField | Reference to checker results |
| `intelligence_output_ref` | CharField | Reference to AI analysis output |
| `notify_output_ref` | CharField | Reference to notification results |
| `intelligence_fallback_used` | BooleanField | Whether AI fallback was used |
| `total_attempts` | PositiveIntegerField | Total pipeline attempts |
| `max_retries` | PositiveIntegerField | Max retries (default: 3) |
| `last_error_type` | CharField | Last error type |
| `last_error_message` | TextField | Last error message |
| `last_error_retryable` | BooleanField | Whether last error is retryable |
| `created_at` | DateTimeField | Creation time |
| `updated_at` | DateTimeField | Last update |
| `started_at` | DateTimeField | Pipeline start time |
| `completed_at` | DateTimeField | Pipeline end time |
| `total_duration_ms` | FloatField | Total execution duration |

Methods: `mark_started()`, `advance_to()`, `mark_completed()`, `mark_failed()`, `mark_retrying()`

#### StageExecution

Per-stage execution details within a pipeline run.

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_run` | ForeignKey | Parent PipelineRun |
| `stage` | CharField (indexed) | INGEST, CHECK, ANALYZE, NOTIFY |
| `status` | CharField | PENDING, RUNNING, SUCCEEDED, FAILED, RETRYING, SKIPPED |
| `attempt` | PositiveIntegerField | Attempt number (1-based) |
| `idempotency_key` | CharField (indexed) | Key for idempotent execution |
| `input_ref` | CharField | Stage input data reference |
| `output_ref` | CharField | Stage output data reference |
| `output_snapshot` | JSONField | Snapshot of stage output (redacted) |
| `error_type` | CharField | Error type |
| `error_message` | TextField | Error message |
| `error_stack` | TextField | Error stack trace |
| `error_retryable` | BooleanField | Whether error is retryable |
| `started_at` | DateTimeField | Stage start time |
| `completed_at` | DateTimeField | Stage end time |
| `duration_ms` | FloatField | Stage execution duration |

Unique constraint: `(pipeline_run, stage, attempt)`

Methods: `mark_started()`, `mark_succeeded()`, `mark_failed()`, `mark_skipped()`

#### PipelineDefinition

Dynamic pipeline configuration for definition-based pipelines.

| Field | Type | Description |
|-------|------|-------------|
| `name` | CharField (unique) | Pipeline identifier |
| `description` | TextField | Pipeline description |
| `version` | PositiveIntegerField | Incremented on updates |
| `config` | JSONField | Pipeline schema (nodes, connections, defaults) |
| `tags` | JSONField | Arbitrary tags |
| `is_active` | BooleanField (indexed) | Whether active |
| `created_by` | CharField | Creator identifier |
| `created_at` | DateTimeField | Creation time |
| `updated_at` | DateTimeField | Last update |

Methods: `get_nodes()`, `get_defaults()`, `get_entry_node()`

### DTOs

#### StageContext (dataclass)

Input to all stage executors.

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | str | Correlation ID |
| `run_id` | str | Pipeline run ID |
| `incident_id` | int or None | Discovered incident |
| `attempt` | int | Which attempt (1, 2, 3...) |
| `environment` | str | production/staging |
| `source` | str | grafana/alertmanager/custom |
| `alert_fingerprint` | str | For dedup |
| `payload` | dict | Raw input |
| `previous_results` | dict | Results from prior stages |

#### Stage Result DTOs

All results share: `errors: list[str]`, `duration_ms: float`, `has_errors` property, `to_dict()` method.

**IngestResult**: `incident_id`, `alert_fingerprint`, `severity`, `alerts_created`, `alerts_updated`, `alerts_resolved`, `incidents_created`, `incidents_updated`

**CheckResult**: `checks` (list), `checks_run`, `checks_passed`, `checks_failed`, `checker_output_ref`

**AnalyzeResult**: `summary`, `probable_cause`, `actions`, `recommendations`, `confidence`, `fallback_used`, `ai_output_ref`

**NotifyResult**: `deliveries` (list), `provider_ids`, `messages`, `channels_attempted`, `channels_succeeded`, `channels_failed`, `notify_output_ref`

#### PipelineResult (dataclass)

Final pipeline output.

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | str | Correlation ID |
| `run_id` | str | Pipeline run ID |
| `status` | str | COMPLETED or FAILED |
| `incident_id` | int or None | Associated incident |
| `ingest` | IngestResult or None | Stage 1 result |
| `check` | CheckResult or None | Stage 2 result |
| `analyze` | AnalyzeResult or None | Stage 3 result |
| `notify` | NotifyResult or None | Stage 4 result |
| `started_at` | datetime | Start time |
| `completed_at` | datetime | End time |
| `total_duration_ms` | float | Total duration |
| `stages_completed` | list[str] | Completed stage names |
| `final_error` | StageError or None | Error if failed |

### Hardcoded Pipeline (PipelineOrchestrator)

Fixed 4-stage sequence:

```
PENDING -> INGESTED -> CHECKED -> ANALYZED -> NOTIFIED (success)
                                      |
                                    FAILED (terminal)
                                      |
                                   RETRYING (resume from last success)
```

**Execution flow**:

1. Create `PipelineRun` with correlation IDs
2. Execute stages sequentially via executors
3. Each stage receives outputs from all previous stages via `previous_results`
4. Incident ID discovered in INGEST, threaded through all subsequent stages
5. Per-stage retries with exponential backoff: `backoff_factor ^ attempt` seconds
6. Failed pipelines can be resumed via `resume_pipeline(run_id, payload)`

**Stage executors**:

| Stage | Executor | Wraps |
|-------|----------|-------|
| INGEST | IngestExecutor | `alerts.services.AlertOrchestrator` |
| CHECK | CheckExecutor | `alerts.check_integration.CheckAlertBridge` |
| ANALYZE | AnalyzeExecutor | `intelligence.providers` (with fallback) |
| NOTIFY | NotifyExecutor | `notify.drivers` (with message building) |

### Definition-Based Pipeline (DefinitionBasedOrchestrator)

Dynamic pipelines configured via `PipelineDefinition.config`:

```json
{
  "version": "1.0",
  "defaults": {"max_retries": 3, "timeout_seconds": 300},
  "nodes": [
    {"id": "check_health", "type": "context", "config": {"checker_names": ["cpu", "memory"]}, "next": "notify"},
    {"id": "notify", "type": "notify", "config": {"drivers": ["slack"]}}
  ]
}
```

**Node types**:

| Type | Handler | Purpose |
|------|---------|---------|
| `ingest` | IngestNodeHandler | Parse alert webhooks |
| `context` | ContextNodeHandler | Run health checkers |
| `intelligence` | IntelligenceNodeHandler | AI analysis |
| `notify` | NotifyNodeHandler | Send notifications |
| `transform` | TransformNodeHandler | Extract, filter, map data |

Each node receives `NodeContext` (with `previous_outputs`) and returns `NodeResult`.

### Monitoring Signals

Emitted at every stage boundary via pluggable backends.

#### SignalTags (dataclass)

Required on every signal: `trace_id`, `run_id`, `stage`, `incident_id`, `source`, `alert_fingerprint`, `environment`, `attempt`, `extra`.

#### Signal Types

| Signal | When Emitted |
|--------|-------------|
| `pipeline.started` | Pipeline begins |
| `pipeline.completed` | Pipeline ends (success or failure) |
| `pipeline.stage.started` | Stage begins |
| `pipeline.stage.succeeded` | Stage completes successfully |
| `pipeline.stage.failed` | Stage fails (includes retryable flag) |
| `pipeline.stage.retrying` | Stage retry initiated |

#### Backends

- **LoggingBackend** (default): Structured JSON logging
- **StatsdBackend**: Sends metrics to StatsD (timing, counters)

### Celery Tasks

| Task | Purpose |
|------|---------|
| `run_pipeline_task` | Execute full pipeline synchronously in worker |
| `resume_pipeline_task` | Resume failed pipeline from last success |
| `start_pipeline_task` | Create PipelineRun and queue async execution |

---

## Data Object Reference

### Models (Database)

| App | Model | Purpose |
|-----|-------|---------|
| alerts | **Alert** | Single alert from external source |
| alerts | **Incident** | Groups related alerts, tracks lifecycle |
| alerts | **AlertHistory** | Audit trail of alert state changes |
| checkers | **CheckRun** | Audit trail of health check executions |
| intelligence | **IntelligenceProvider** | Provider config with single-active enforcement |
| intelligence | **AnalysisRun** | Audit trail of AI analysis executions |
| notify | **NotificationChannel** | Named channel config (driver + credentials) |
| orchestration | **PipelineRun** | Pipeline execution record with correlation IDs |
| orchestration | **StageExecution** | Per-stage execution details within pipeline |
| orchestration | **PipelineDefinition** | Dynamic pipeline configuration |

### DTOs (Dataclasses)

| App | DTO | Purpose |
|-----|-----|---------|
| alerts | **ParsedAlert** | Normalized alert from any webhook driver |
| alerts | **ParsedPayload** | Result of parsing a webhook payload |
| alerts | **ProcessingResult** | Result of alert processing (counts) |
| checkers | **CheckResult** | Standardized result from any checker |
| checkers | **CheckStatus** | Enum: OK, WARNING, CRITICAL, UNKNOWN |
| intelligence | **Recommendation** | Single recommendation from analysis |
| intelligence | **RecommendationType** | Enum: MEMORY, DISK, CPU, PROCESS, NETWORK, GENERAL |
| intelligence | **RecommendationPriority** | Enum: LOW, MEDIUM, HIGH, CRITICAL |
| notify | **NotificationMessage** | Message to be delivered via any driver |
| orchestration | **StageContext** | Input context for stage executors |
| orchestration | **IngestResult** | Stage 1 output |
| orchestration | **CheckResult** | Stage 2 output |
| orchestration | **AnalyzeResult** | Stage 3 output |
| orchestration | **NotifyResult** | Stage 4 output |
| orchestration | **PipelineResult** | Final pipeline output |
| orchestration | **NodeContext** | Input context for definition-based nodes |
| orchestration | **NodeResult** | Output from definition-based nodes |
| orchestration | **SignalTags** | Required metadata for monitoring signals |

### Data Flow Through Pipeline

```
Webhook JSON
    |
    v
ParsedPayload (alerts: [ParsedAlert, ...])
    |  -> creates Alert, Incident, AlertHistory
    v
ProcessingResult {alerts_created, incidents_created, ...}
    |  -> stored as IngestResult in StageContext.previous_results
    v
CheckResult {status, message, metrics}  (per checker)
    |  -> stored as orchestration CheckResult in previous_results
    v
[Recommendation, ...]  (from AI or local provider)
    |  -> stored as AnalyzeResult in previous_results
    |  -> creates AnalysisRun audit record
    v
NotificationMessage {title, message, severity}
    |  -> sent via driver.send()
    v
NotifyResult {deliveries, channels_succeeded, ...}
    |
    v
PipelineResult (final output with all stage results)
```