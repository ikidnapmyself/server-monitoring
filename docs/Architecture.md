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
`check:{instance_id}:{checker_name}` under `source: cluster`, and its incident groups on
`(subject, instance)` with subject `checker:<name>`. `instance_id` (from `INSTANCE_ID`, hostname when unset) is
used rather than the hostname because hostnames collide across stock installs and change
on rename. Alert identity is the pair `(fingerprint, source)` and incident grouping
matches on the alert *name*, so both producers — the local bridge and the pushed payload —
must agree on all three or one condition on one machine splits into several rows and
several incidents.

**No lane of its own.** The hub's checker traffic routes through `cluster-nodes` like any
node's; the old record-only `hub-self-check` lane is retired (deactivated, not deleted, by
migration `0018`) so the hub can page about itself. See
[Deployment → Routing](Deployment.md).

### Describers

Nothing stores display text. Each app describes its own output when it's read, as a `Description` from `config/describing.py`: a headline, a tone, an optional detail and action, and facts. Checkers describe their results through `BaseChecker.describe`, using each checker's `label` and `problem`. `apps/alerts/describing.py` describes alerts and incidents, so an incident's headline ("High CPU usage on web-1") comes from its lead alert and node, not from a column. Each stage describes what it stored: `apps/checkers/describing.py` for check results, `apps/orchestration/describing.py` for runs and stage outcomes, `apps/intelligence/describing.py` for analyses and `apps/notify/describing.py` for deliveries. A failed run leads with its reason from the `REASONS` registry, such as "no route matches this incident", and a failed call to an outside service leads with its kind from `config/failures.py`, such as an expired API key or a rate limit. Readiness faults come from `config/readiness.py` the same way. The console and notifications render these descriptions and never decode raw rows. An action names an intent, and the console's `apps/console/intents.py` resolves it to a link or a button the viewer has permission for. It is the one place in the console that builds an admin URL. Identifiers, error text and raw JSON stay in a Details disclosure.

### Stage Configuration

Stage behavior is controlled through routes in the console, not environment variables:

- **Routing**: `PipelineDefinition` (console, `/console/routes/`) matches the run's subject alert and its ordered `stages` list selects which downstream stages run; its single `channel` is the notify target. Unmatched traffic fails non-retryably as `no_route`. There is no implicit fallback.
- **Intelligence**: The `IntelligenceProvider` model (console, `/console/providers/`) controls which AI provider is active.
- **Notify**: The `NotificationChannel` model (console, `/console/channels/`) controls which channels are active via `is_active`.

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

### Break-glass admin

Operators sign in at `/console/login/` and work in the console. `/admin/` is stock Django
for active superusers only, at the site and at its login. Every model is editable there,
except that channels, providers and API keys are added only in the console. The four
secret fields, `NotificationChannel.config`, `IntelligenceProvider.config`,
`AnalysisRun.provider_config` and `APIKey.key`, are excluded from every form and can't be
used as changelist filters. An admin edit writes the raw row. It skips the console's
version and digest checks and `IncidentManager.transition`, writes no `AlertHistory`, and
enqueues no run.

#### Routes

`/console/routes/` renders the routing table as a read-time projection
(`config/netmap.py`). It stores nothing and lists `PipelineDefinition` rows in the
exact order `resolve_pipeline` consults them, under the console's **System** item.
Each route shows its conditions in words, its stages, and a state when it takes no
traffic: **Off**, **Never matches** (a malformed match), or **Never reached** (an
earlier route provably matches everything this one would, and is named). A delivery
line mirrors `delivery_gap()` 1:1: delivers to a channel, records only (no `notify`
stage), or can't deliver (no active channel, or a driver this hub doesn't have).
The map returns `Action`s, never URLs. A reader with `view_pipelinedefinition`
sees the page. The edit links need `change_pipelinedefinition`.

`/console/routes/<pk>/` edits one route and `/console/routes/new/` adds one (it needs
`add_pipelinedefinition`). Conditions are rows: field, rule and value, all of which must
hold. A save carries the version it opened, and `save_route` writes only if the route is
still at that version, so a route changed in between answers 409 with its fresh values. A
stored match the rows can't reproduce is kept as it is unless the operator ticks "Replace
them". A reader with only `view_pipelinedefinition` sees the same form, disabled. Tags stay
out of the form and show in Details. `/console/routes/<pk>/delete/` confirms first and
says where the route's traffic goes.

#### Channels

`/console/channels/` lists the notification channels and the routes on each, under the
same **System** item. `/console/channels/new/` picks a driver, then shows the settings
that driver declares. `/console/channels/<pk>/` edits one. A secret is never rendered:
its box stays empty, and leaving it blank keeps the stored value. Settings the driver
doesn't declare are kept and named in Details. A save carries a digest of the channel it
opened, so a channel changed in between answers 409. `/console/channels/<pk>/delete/`
names the routes that would stop delivering.

#### Fleet policy

`/console/policy/` answers "which machines have I overridden, and is any of it doing
nothing?" A row exists per node and checker where the node holds a config entry **or**
has a firing alert, so a checker alerting with no policy at all stops looking like a
machine with nothing wrong. Problem rows sort first.

Each row prints the checker, its stored policy, one of five states, why it is not
simply working, what it is doing now (`3 firing, 2 would change`), when a
re-evaluation last visibly changed something, and a link into that checker's own
boxes on the console policy editor. Three of the states come from config
(**In effect**, **Saved but not scoring**, **Not honoured**); two come from a firing
alert with no matching config: **No policy set**, which is a gap an operator can close,
and **Not re-evaluatable**, which is muted because no scorer reads that checker and
nothing can be done about it.

A row that can be scored also carries a **Re-evaluate** button. It scopes the console
re-evaluate page to that one checker. The page itself writes nothing: it reads `Node.config` through
`apps.alerts.node_policy.build_effective_policy`, scores firing alerts with the same
`_outcome_for` the confirm preview uses, and reads `AlertHistory` for the applied
column, in three queries that do not grow with the node count. Access is
`alerts.view_node` or `change_node`. Only `change_node` gets the edit and re-evaluate
links.

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
- **Retry:** the console's Retry moves a failed run back to `PENDING`. The next drain skips
  the stages that already succeeded. `RETRYING` is set only by the resume API.

Routing pipelines are managed in the console (`/console/routes/`) or set up by the
guided `setup_cluster`. The legacy node/edge graph engine was retired
in Phase D — `PipelineDefinition` is now purely a routing rule (match → ordered stages → one channel).

**Observability:** the console incident page's timeline and the console run page,
`manage.py trace <alert|trace_id>`, and `manage.py report` (per-node incidents, per-pipeline routing
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

Environment variables configure **infrastructure only**. Application behavior (which checkers to run, intelligence provider, notification channels) is managed through the console. Routes, notification channels, AI providers and API keys live in the console at `/console/routes/`, `/console/channels/`, `/console/providers/` and `/console/keys/`.

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
