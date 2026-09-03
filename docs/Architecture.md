---
title: Architecture
layout: default
nav_order: 2
---

# Architecture

## Overview

Server-maintanence is a Django-based server monitoring and alerting system. It ingests alerts from external sources, runs health checks, generates AI-powered recommendations, and dispatches notifications. Producers write alert truth; alerts roll into incidents; the orchestrator runs one pipeline per incident that materially changed.

**Tech stack:** Django 5.2, psutil (system metrics), Jinja2 (notification templates). The pipeline is broker-free — durable ingest + a `process_inbox` drain, no message broker.

## Producers, then stages

Producing an alert is **not** a pipeline stage. A producer writes alerts, lets incidents form,
and hands the materially changed ones to `apps.orchestration.intake.enqueue_for`, which records
one `PENDING` `PipelineRun` per incident with the payload `{"downstream_incident_id": N}`:

```
produce truth  →  roll into incidents  →  enqueue one run per changed incident  →  drain
```

`enqueue_for(..., sync=True)` drains those runs before returning — how `check_health` and
`run_pipeline` finish the job for an operator with no daemon. Without it, `process_inbox`
takes them. Same rows, same lanes, same executors; `sync` only decides who runs them and when.

Six producers walk through that one door: the alerts webhook (`apps/alerts/views.py`), the
`/orchestration/pipeline/` endpoint, `push_to_hub --local`, `check_health`, `run_pipeline`, and
operator transitions on `IncidentManager`.

**The incident is the subject of the run.** The orchestrator then executes an ordered subset of
three stages, chosen by the matched `PipelineDefinition`:

```
        ┌─────────┐    ┌─────────┐    ┌─────────┐
run  ──▶│  CHECK  │───▶│ ANALYZE │───▶│ NOTIFY  │
(an     │checkers │    │  intel  │    │ notify  │
 incident)└───────┘    └─────────┘    └─────────┘
```

| Stage | App | What it does | Input | Output |
|-------|-----|-------------|-------|--------|
| **CHECK** | `apps.checkers` | Diagnose the incident: run the checkers named by the distinct `checker` labels on the incident's alerts, filtered to the local registry. **No labels means it runs nothing** — it does not sweep the whole registry | The incident and its alerts | `CheckResult` (status, metrics) |
| **ANALYZE** | `apps.intelligence` | Generate AI recommendations via provider pattern (local/OpenAI) | Incident + check results | `AnalyzeResult` (recommendations) |
| **NOTIFY** | `apps.notify` | Dispatch notifications via driver pattern (email, Slack, PagerDuty) | Analysis results | `NotifyResult` (delivery status) |

`INGEST` is retired as a stage. The enum value survives so historical `StageExecution` rows still
render, and `apps/alerts/diagnosis.py` reports it only for incidents that have a legacy run. A run
whose payload is a legacy `{"driver", "payload"}` pair still drains, so pushes in flight at deploy
time are not lost; that branch is legacy-only and dies once no such rows remain.

The **orchestration app** (`apps.orchestration`) controls all stage transitions. Stages never call downstream stages directly.

### Use Cases

Not every deployment uses all three stages. The lane is composable — pick the stages you need:

**Local server monitoring** — You want to monitor CPU, memory, and disk on this machine and get notified when something is wrong. No external monitoring tools required. Health checks run on a cron schedule, generate alerts locally, and dispatch notifications.

```
Checkers -> Notify                      (local-monitor)
Checkers -> Intelligence -> Notify      (local-smart, adds AI analysis)
```

**External alert processing** — You already use Grafana, AlertManager, PagerDuty, or other monitoring tools. This system receives their webhooks, optionally enriches them with local health checks and AI analysis, and forwards notifications to your preferred channels.

The incoming alert is written by the producer before any lane is chosen; the lane names only
the stages that follow.

```
(alert written) -> Notify                               (direct)
(alert written) -> Checkers -> Notify                   (health-checked)
(alert written) -> Intelligence -> Notify               (ai-analyzed)
(alert written) -> Checkers -> Intelligence -> Notify   (full lane)
```

**Central alert hub** — This server acts as an aggregation point for multiple monitored servers. It receives webhooks from various sources, runs AI analysis, and dispatches notifications. No local health checks needed.

```
Alert -> Intelligence -> Notify         (ai-analyzed)
```

See the [Setup Guide](Setup-Guide) for step-by-step walkthroughs.

### Hub Self-Monitoring

**A hub is a node in its own registry.** Whenever a machine records checker results
locally, `CheckAlertBridge` upserts that machine into the `Node` table
(`last_source = "local"`; a push from another machine sets `"cluster"`). So the box
running the hub appears in the fleet it aggregates, and its own full disk becomes an
alert, an incident and a page exactly like any agent's.

Two delivery modes, and when each applies:

| Mode | Command | What happens | Use when |
|------|---------|--------------|----------|
| **Inline** | `check_health` | Runs the checkers, records `Alert`/`Incident` rows, enqueues one run per materially changed incident and **drains them before returning**. Records by default; `--no-alert` prints only, `--no-notify` runs the lane minus NOTIFY | The synchronous local entrypoint. A single machine with no hub, no cron and nobody draining an inbox still gets its analysis |
| **Scheduled** | `run_pipeline --checks-only` *(deprecated — use `check_health`)* | Identical work by an identical path: the bridge records the alerts, one run is enqueued per materially changed incident, and the command drains them before returning. Warns on stderr, naming `check_health`; kept because an operator still types it by hand | Nothing schedules it any more — `bin/install/cron.sh` schedules `check_health --json` |
| **Through the inbox** | `push_to_hub --local` | Runs the checkers, writes their alerts here through `CheckAlertBridge`, and enqueues one `PENDING` `PipelineRun` per materially changed incident for `process_inbox` to drain | You want the hub's own checks queued and retried like a peer's push. Needs a running `process_inbox`, so it is not scheduled by the installer |

`bin/install/cron.sh` schedules `check_health --json` on every machine, and adds a
**push to hub** job only where `HUB_URL` is set. A machine without a hub needs no second
job: the health check already routes and notifies on its own.

**One identity, both producers.** A checker-origin alert is fingerprinted
`check:{instance_id}:{checker_name}` under `source: cluster`, with the stable name
`"<CHECKER> Check Alert"`. `instance_id` (from `INSTANCE_ID`, hostname when unset) is
used rather than the hostname because hostnames collide across stock installs and change
on rename. Alert identity is the pair `(fingerprint, source)` and incident grouping
matches on the alert *name*, so both producers — the local bridge and the pushed payload —
must agree on all three or one condition on one machine splits into several rows and
several incidents.

**No lane of its own.** The hub's checker traffic routes through `cluster-nodes` like any
node's; the old record-only `hub-self-check` lane is retired (deactivated, not deleted, by
migration `0018`) so the hub can page about itself. See
[Deployment → Routing](Deployment.md).

### Stage Configuration

Stage behavior is controlled through routing pipelines and Django Admin — not environment variables:

- **Routing**: `PipelineDefinition` (Django Admin) matches the run's subject alert and its ordered `stages` list selects which downstream stages run; its single `channel` is the notify target. Unmatched traffic fails non-retryably as `no_route` — there is no implicit fallback.
- **Intelligence**: The `IntelligenceProvider` model (Django Admin) controls which AI provider is active.
- **Notify**: The `NotificationChannel` model (Django Admin) controls which channels are active via `is_active`.

## Entry Points

### Management Commands

| Command | App | Purpose |
|---------|-----|---------|
| `check_health [checkers...]` | checkers | **The local entrypoint.** Runs health checks, displays a summary, records their alerts, enqueues one run per materially changed incident and drains them before returning — analysis and notification included, with no daemon. Flags: `--no-alert` (print only, write nothing, so no incident forms and nothing is enqueued), `--no-notify` (run the matched lane without NOTIFY — look at a machine, page nobody), `--list`, `--json`, `--fail-on-warning`, `--fail-on-critical` |
| `run_check <checker>` | checkers | Run a single checker with checker-specific options (`--samples`, `--per-cpu`, `--paths`, `--hosts`, `--names`) |
| `run_pipeline --checks-only` | orchestration | **Deprecated — use `check_health`.** Runs this machine's checkers, records their alerts and drains the incident runs they earn. Additional flags: `--checkers`, `--no-incidents` (record alerts, create no incidents, so nothing is enqueued or routed), `--no-notify` (run the matched lane without NOTIFY — look at a node in real time, get the analysis, page nobody), `--hostname`, `--label`, `--warning-threshold`, `--critical-threshold` |
| `get_recommendations` | intelligence | Get system recommendations. Flags: `--incident-id`, `--memory`, `--disk`, `--provider`, `--json`, `--list-providers` |
| `test_notify [driver]` | notify | Test notification delivery. Flags: per-driver config (`--webhook-url`, `--smtp-host`, etc.) |
| `run_pipeline` | orchestration | Replay an alert payload: ingest it here, then drain the runs its incidents earn. Flags: `--sample`, `--payload`, `--file`, `--source` (driver name; `cli` auto-detects), `--environment`, `--trace-id`, `--dry-run`, `--json` (`{trace_id, incidents, alerts, errors}`), `--no-notify`, `--checks-only` (deprecated) |
| `monitor_pipeline` | orchestration | View pipeline run history. Flags: `--limit`, `--status`, `--run-id` |
| `push_to_hub` | alerts | Run the checkers and push the results to a hub. Flags: `--local` (record a `PENDING` run here instead of POSTing), `--dry-run`, `--json`, `--checkers` |

### HTTP Endpoints

**Alerts** (`/alerts/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/alerts/webhook/` | Receive alert (auto-detect driver) |
| POST | `/alerts/webhook/<driver>/` | Receive alert (specific driver: alertmanager, grafana, pagerduty, datadog, newrelic, opsgenie, zabbix, generic) |

**Intelligence** (`/intelligence/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/intelligence/health/` | Health check |
| GET | `/intelligence/providers/` | List available AI providers |
| POST | `/intelligence/recommendations/` | Get recommendations for an incident |
| POST | `/intelligence/memory/` | Memory-specific analysis |
| POST | `/intelligence/disk/` | Disk-specific analysis |

**Notify** (`/notify/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/notify/send/` | Send notification (auto-detect driver) |
| POST | `/notify/send/<driver>/` | Send notification (specific driver) |
| POST | `/notify/batch/` | Batch send multiple notifications |
| GET | `/notify/drivers/` | List available drivers |
| GET | `/notify/drivers/<driver>/` | Driver detail and config requirements |

**Orchestration** (`/orchestration/`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/orchestration/pipeline/` | Ingest a payload, leave a PENDING run per changed incident for the drain |
| POST | `/orchestration/pipeline/sync/` | The same, draining those runs before responding |
| GET | `/orchestration/pipelines/` | List pipeline runs |
| GET | `/orchestration/pipeline/<run_id>/` | Get pipeline run status |
| POST | `/orchestration/pipeline/<run_id>/resume/` | Resume a failed pipeline |

### Durable ingest / drain

The pipeline is broker-free. The webhook writes the payload's alerts inline, then
records one `PENDING` `PipelineRun` per materially changed incident (the async trigger
endpoint records one too); `manage.py process_inbox` claims and executes them
(supervised `--loop` or cron). No message broker. See
[Deployment → Durable ingest & the inbox drain](Deployment.md).

### Django Admin

All apps register their models at `/admin/`:

| Admin Path | Models |
|------------|--------|
| `/admin/alerts/` | Alert, Incident, AlertHistory, Node |
| `/admin/checkers/` | CheckRun |
| `/admin/intelligence/` | AnalysisRun |
| `/admin/notify/` | NotificationChannel |
| `/admin/orchestration/` | PipelineRun, StageExecution, PipelineDefinition |

#### Network map

`/admin/map/` (staff-only) renders the routing table as a read-time projection —
no models, no stored state, just `PipelineDefinition` rows in the exact order
`resolve_pipeline` consults them. Each lane shows its match conditions, stage list,
and a state banner: **ok**, **shadowed** (an earlier lane provably matches everything
this one would, and is named), **inactive**, or **never matches** (malformed match).
Below that, a delivery line mirrors `delivery_gap()` 1:1: bound to a channel,
recording only (no `notify` stage), no channel (none configured, or the bound
channel is inactive), or channel driver not registered.

## Pipeline Execution

**Location:** `apps/orchestration/orchestrator.py`

Three stages — CHECK → ANALYZE → NOTIFY — each with a dedicated executor class.
Producing an alert is no longer a stage a producer enters on: every producer writes its
own alerts and enqueues one run per materially changed incident
(`apps.orchestration.intake.enqueue_for`), so INGEST survives only to drain runs
recorded before that change — a legacy `{"driver", "payload"}` payload, which keeps
pushes that were in flight at deploy time from being lost. CHECK is not legacy: it is a
live stage that diagnoses the incident, running the checkers named by the distinct
`checker` labels on that incident's alerts, filtered to the local registry (no labels,
no checkers). The pipeline's *shape* is data, not code: a run
resolves the matching `PipelineDefinition` from its incident's subject alert
(`routing.py`, first-match-wins by `priority`, ties on `id`) and runs the
downstream stages listed in its `stages` column, in that order; NOTIFY sends to the
matched pipeline's single `channel`. A no-match is a non-retryable `no_route`
failure — migration `0012` seeds a `catch-all` lane so unmatched traffic is a row
an operator can read and edit rather than a constant in the orchestrator.

**One run per incident event, not per push.** A producer does not orchestrate: it writes
alerts and returns. Every incident it *materially changed* — created, severity moved, status
transitioned, or its per-checker context key changed — becomes its own `PENDING`
downstream run that resolves its own lane and runs it. A steady-state re-push that says
nothing new starts no run at all, which is also what keeps a five-minute cron from
re-notifying ~288 times a day. Downstream runs inherit the push's `trace_id` with their
own `run_id`, so one push still reads as one story in `manage.py trace`; they are
drained by `process_inbox` like any other inbox work. Operator transitions
(`IncidentManager.acknowledge`/`resolve`/`close`) are one of the six producers of these runs
(`origin=manual`), so an acknowledgement or resolution is announced on the next drain,
and the headline reads the incident's live status. Migration `0016` seeds
`resolved-all-clear`, which notifies an all-clear without paying for an AI analysis of
something that has already recovered. See
`docs/plans/2026-08-19-incident-fanout-design.md`.

- **Endpoints:** `POST /orchestration/pipeline/` ingests the payload inline and leaves
  one `PENDING` run per materially changed incident for the `process_inbox` drain
  (202, `{status, trace_id, incidents}`); `/pipeline/sync/` drains those runs before
  responding (200, plus `alerts` and `errors` counts). It is a producer like the
  webhook, not a separate path — `sync` only decides who executes the runs.
- **CLI:** `python manage.py run_pipeline --sample` / `--dry-run` (and the deprecated
  `--checks-only`, whose work `check_health` now does).
- **Resume:** failed pipelines resume from the last successful stage.

Routing pipelines are managed in **Django Admin** (`/admin/orchestration/pipelinedefinition/`)
or wired by the guided `setup_cluster`. The legacy node/edge graph engine was retired
in Phase D — `PipelineDefinition` is now purely a routing rule (match → ordered stages → one channel).

**Observability:** a "Journey" panel on the Alert/Incident admin, `manage.py trace
<alert|trace_id>`, and `manage.py report` (per-node incidents, per-pipeline routing
hits, inbox depth) — all read-only projections over the `trace_id` chain.

## Data Models

### Core Models

```
Alert ──────┐
AlertHistory│──▶ Incident ──▶ PipelineRun ──▶ StageExecution
            │                      │
CheckRun ◀──┘                      │
AnalysisRun ◀──────────────────────┘
NotificationChannel (standalone config)
PipelineDefinition (standalone config)
```

| Model | App | Purpose |
|-------|-----|---------|
| `Alert` | alerts | Normalized alert record (fingerprint, status, severity, labels, raw payload) |
| `Incident` | alerts | Groups related alerts, tracks lifecycle (open → ack → resolved → closed) |
| `AlertHistory` | alerts | Audit trail of alert state transitions |
| `Node` | alerts | Registry of every machine that reports on itself, keyed by `instance_id` — this hub included |
| `CheckRun` | checkers | Health check execution log (status, metrics, timing, trace_id) |
| `AnalysisRun` | intelligence | AI analysis execution log (provider, status, timing, recommendations) |
| `PipelineRun` | orchestration | Pipeline execution tracking (status, timing, correlation IDs) |
| `StageExecution` | orchestration | Per-stage execution within a pipeline (input/output snapshots) |
| `NotificationChannel` | notify | Persistent channel configuration (driver, config, enabled) |
| `PipelineDefinition` | orchestration | Routing rule: match -> ordered stages -> one notify channel |

### State Machine

Pipeline runs progress through:

```
PENDING → INGESTED → CHECKED → ANALYZED → NOTIFIED (success)
                                    └──→ FAILED (terminal)
                                    └──→ RETRYING → (resume from last stage)
```

### Correlation IDs

Every pipeline run carries:
- `trace_id` — Correlation ID for tracing across all stages, logs, and DB records
- `run_id` — Unique ID for the specific pipeline run

## Configuration

### Key Environment Variables

Environment variables configure **infrastructure only**. Application behavior (which checkers to run, intelligence provider, notification channels) is managed through Django Admin and pipeline definitions.

| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | Required in production |
| `DJANGO_DEBUG` | Debug mode | `0` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `*` |
| `INBOX_DEPTH_WARN` | Drain backlog warning threshold | `500` |
| `ORCHESTRATION_MAX_RETRIES_PER_STAGE` | Retries before pipeline failure | `3` |
| `ORCHESTRATION_BACKOFF_FACTOR` | Exponential backoff multiplier | `2.0` |
| `ORCHESTRATION_INTELLIGENCE_FALLBACK_ENABLED` | Continue pipeline when AI fails | `1` |
| `ORCHESTRATION_METRICS_BACKEND` | Metrics backend (`logging` or `statsd`) | `logging` |
| `STATSD_HOST` | StatsD server host | `localhost` |
| `STATSD_PORT` | StatsD server port | `8125` |
| `STATSD_PREFIX` | StatsD metric prefix | `pipeline` |

### Settings

Django settings live in `config/settings.py`. Copy `.env.sample` to `.env` for local development.
