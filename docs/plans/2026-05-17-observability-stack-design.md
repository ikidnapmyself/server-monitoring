---
title: "2026-05-17 Observability Stack Design"
parent: Plans
---

# Observability Stack Design

**Status:** Brainstormed 2026-05-17. Implementation plan pending.
**Owner:** Observability rollout (Claude-assisted, human-approved).

## Overview

Four operator-visible items get designed as one coherent stack so they share the same on-disk schema, the same dispatch pathway, and the same CLI surface:

1. **Log consistency** — structured JSON-lines on disk, automatic cross-cutting fields (`trace_id`, `run_id`, `incident_id`, `stage`, `source`) on every record.
2. **CLI log reader** — `cli logs view|tail|trace|heartbeats` with rich filters, JSON-aware output, cluster-aware pivot.
3. **Cronjob freshness checker** — explicit heartbeat helper, in-code registry of expected jobs, freshness alerts that flow through the existing `alerts → orchestration → notify` pipeline.
4. **Logbook for disconnected nodes** — agent → hub periodic batch push of new JSONL chunks; hub keeps per-instance history on disk; CLI reader on the hub pivots by instance. **Cluster topology** (who pushes to whom, discovery, mesh vs fan-in) is deferred to a separate brainstorm.

The unifying constraint: only `apps.notify` dispatches notifications. `apps.observability` is a pure event producer — when it needs to fire an alert, it creates an `Alert` through the existing ingestion path via a new in-process `internal` alert driver. No new notification channels, no parallel cooldown logic, no extra env vars in observability.

## Goals & non-goals

**Goals.**
- One canonical structured log stream (`events.jsonl`) plus a small sidecar (`heartbeats.jsonl`) for freshness signals.
- Cross-cutting fields populated automatically via `ContextVar`s set at three entry points (HTTP middleware, Celery `task_prerun`, orchestrator `start_pipeline`). Existing ~85 log call sites untouched.
- Per-job heartbeat helper that produces alerts through the standard pipeline. No new notification config.
- CLI reader that filters, follows, and pivots across cluster instances.
- Agent → hub log forwarding that survives disconnects and resumes without loss.

**Non-goals.**
- Cluster topology design (separate brainstorm).
- Hub-side analytics / dashboards beyond admin "last_used_at" visibility.
- Long-term metrics storage (this is a log stack, not a metrics stack).
- Backwards compatibility with a deployed system — system has never been deployed; legacy paths get removed in the same PR.

## Architecture

```
┌─────────────────────────── one host / agent ───────────────────────────┐
│                                                                        │
│  Django request / Celery task / management command                     │
│         │                                                              │
│         ▼  (entry hook sets contextvars: trace_id, run_id, …)          │
│   ┌──────────────────────────────────────────────────────────────────┐ │
│   │  apps.observability                                              │ │
│   │  ├─ context.py         — trace_id / run_id / incident_id / …     │ │
│   │  ├─ formatter.py       — JsonLineFormatter + PrettyConsole       │ │
│   │  ├─ middleware.py      — HTTP request hook → set context         │ │
│   │  ├─ celery_signals.py  — task_prerun / task_postrun              │ │
│   │  ├─ heartbeat.py       — emit_heartbeat / heartbeat() ctx mgr    │ │
│   │  ├─ heartbeat_registry.py                                        │ │
│   │  ├─ heartbeat_reader.py                                          │ │
│   │  ├─ checks.py          — H001 / H002 / H003 system checks        │ │
│   │  ├─ views/cluster_push.py                                        │ │
│   │  └─ management/commands/                                         │ │
│   │     ├─ read_logs.py                                              │ │
│   │     ├─ check_heartbeats.py                                       │ │
│   │     └─ push_logs_to_hub.py                                       │ │
│   └────┬──────────────────────┬──────────────────────┬────────────────┘ │
│        ▼ events.jsonl         ▼ heartbeats.jsonl    ▼ console (TTY)     │
│   LOGS_DIR/events.jsonl   LOGS_DIR/heartbeats.jsonl                     │
│                                                                        │
│  freshness checker (check_heartbeats / system check)                   │
│      reads heartbeats.jsonl → for any stale registered name,           │
│      calls AlertOrchestrator.process_webhook(payload, driver="internal")│
│         → ingest → check → analyze → notify (existing pipeline)        │
│                                                                        │
│  CLI: bin/cli/logs.sh → manage.py read_logs (view | tail | trace)      │
└────────────────────────────────────────────────────────────────────────┘
                        │ (cluster mode only)
                        ▼  agent ── periodic batch push ─▶
                        ┌─────────── hub (any Django host) ─────────────┐
                        │ POST /cluster/logs/<stream>/                  │
                        │   → append to                                 │
                        │     LOGS_DIR/cluster/<api_key.name>/          │
                        │       events.jsonl                            │
                        │       heartbeats.jsonl                        │
                        │ cli logs --instance <name> ←──────────────────┘
                        └───────────────────────────────────────────────┘
```

**New app: `apps.observability`.** Owns: context, formatters, the heartbeat helper, the registry/reader, the system checks, the cluster push view, the read-logs / check-heartbeats / push-logs-to-hub management commands. Three integration points outside the app:

- `config/middleware/observability.py` — HTTP request middleware (runs after `APIKeyAuthMiddleware`).
- `config/celery.py` — `task_prerun` / `task_postrun` signal handlers.
- `apps/orchestration/orchestrator.py` — one `bind(...)` block in `start_pipeline()` and `_execute_stage_with_retry()`.

**One new alert driver: `InternalDriver` in `apps/alerts/drivers/internal.py`.** Used only by in-process callers (the freshness checker); not webhook-reachable; no `signature_header`.

**No new model.** Cluster identity reuses `APIKey.name` as the `instance_id`. Cluster "last seen" reuses `APIKey.last_used_at`. The freshness check that an agent is still pushing iterates active `APIKey`s with `allowed_endpoints` containing `/cluster/logs/`.

## Schema & streams

### Common fields (every record, both streams)

| Field | Type | Source | Required |
|---|---|---|---|
| `ts` | string | UTC ISO-8601 with `Z` suffix (e.g. `2026-05-17T14:23:01.482Z`) | yes |
| `level` | string | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` | yes |
| `logger` | string | Python logger name, e.g. `apps.alerts.services` | yes |
| `msg` | string | Formatted message | yes |
| `instance_id` | string | `settings.INSTANCE_ID` or `socket.gethostname()` fallback | yes |
| `v` | int | Schema version (`1` initially) | yes |

### Conditional context fields (populated when a `ContextVar` is set)

| Field | Type | Set by |
|---|---|---|
| `trace_id` | string (uuid4) | `apps.observability.middleware`, `celery_signals`, orchestrator |
| `run_id` | string (uuid4) | orchestrator `start_pipeline` |
| `incident_id` | int | orchestrator after ingest |
| `stage` | string | orchestrator per-stage execution |
| `source` | string | orchestrator from `PipelineRun.source` |

### Per-call optional fields

| Field | Type | Notes |
|---|---|---|
| `category` | string | `pipeline`, `http`, `cron`, `cluster`, `admin`, `checks`, `internal`. Defaults from `logger`-prefix mapping; overridable via `extra={"category": ...}`. |
| `extra` | object | Any `extra={...}` kwargs the call site passes (minus reserved keys). |
| `exc_type`, `exc_msg`, `exc_stack` | string | When `logger.exception(...)` or `exc_info=True`. |

### Heartbeat-only fields (`heartbeats.jsonl`)

| Field | Type | Notes |
|---|---|---|
| `name` | string | Heartbeat key, e.g. `check_health.hourly`. |
| `status` | string | `ok` / `fail` / `running`. |
| `duration_ms` | float | Optional — total job duration. |
| `metrics` | object | Optional — caller-supplied numeric/string metrics. |

### Example records

**events.jsonl** (HTTP request inside a pipeline run):

```json
{"ts":"2026-05-17T14:23:01.482Z","v":1,"level":"INFO","logger":"apps.alerts.services","msg":"Created incident from grafana payload","instance_id":"prod-1","trace_id":"7c3a…","run_id":"4ef1…","incident_id":204,"category":"alerts","extra":{"severity":"warning"}}
```

**heartbeats.jsonl** (cron job completion):

```json
{"ts":"2026-05-17T14:00:03.117Z","v":1,"level":"INFO","logger":"apps.observability.heartbeat","msg":"heartbeat","instance_id":"prod-1","name":"check_health.hourly","status":"ok","duration_ms":482.6,"metrics":{"checks_run":7,"checks_failed":0}}
```

### Stream behaviour

- **`events.jsonl`** — append-only, size-rotated (default 50 MB × 5 backups → ≈250 MB cap per host). All app logs.
- **`heartbeats.jsonl`** — append-only, size-rotated (default 5 MB × 3 backups → ≈20 MB cap). Freshness checker reads the live file plus the most recent rotated backup to handle the rotation boundary.
- Both use `logging.handlers.RotatingFileHandler`. Thresholds and backup counts via `OBSERVABILITY_*` env vars.
- The existing `LOGS_DIR/django.log` and `LOGS_DIR/checks.log` are **removed** in the same PR (system never reached production; no migration window needed).

## ContextVars plumbing

### Context module (`apps/observability/context.py`)

```python
from contextvars import ContextVar

trace_id_var:    ContextVar[str | None] = ContextVar("trace_id", default=None)
run_id_var:      ContextVar[str | None] = ContextVar("run_id", default=None)
incident_id_var: ContextVar[int | None] = ContextVar("incident_id", default=None)
stage_var:       ContextVar[str | None] = ContextVar("stage", default=None)
source_var:      ContextVar[str | None] = ContextVar("source", default=None)

def bind(**fields):
    """Set multiple context fields atomically. Returns a token to restore."""

def snapshot() -> dict:
    """Read all current values as a dict (formatter calls this per record)."""
```

### Entry hooks — three, all small

1. **`config/middleware/observability.py`** — HTTP middleware, runs after `APIKeyAuthMiddleware`. Reads `X-Trace-Id` header or generates a new `uuid4()`; binds `trace_id`, `source="http"`. Restores in a `try/finally`.
2. **`config/celery.py`** — `task_prerun` / `task_postrun` Celery signals. Binds `trace_id` from task kwargs/headers, `source="celery"`. Clears in `task_postrun`.
3. **`apps/orchestration/orchestrator.py`** — `bind(trace_id, run_id, incident_id, stage, source)` in `start_pipeline()` and `_execute_stage_with_retry()`. Existing `extra={"trace_id": ...}` keyword usage on logger calls remains valid; formatter merges record extras with the contextvar snapshot.

### Formatters

**`JsonLineFormatter`** (file handler):

- Builds a dict: `{ts, v, level, logger, msg, instance_id, **snapshot()}`.
- Resolves `category` from `record.__dict__["category"]` if set via `extra=`, else from a logger-prefix table.
- Merges `record.__dict__` keys that came from `extra={}` (after stripping the standard `logging.LogRecord` reserved keys) into an `extra` sub-object.
- On exception: serialises `exc_info` into `exc_type` / `exc_msg` / `exc_stack`.
- `json.dumps(..., default=str, ensure_ascii=False)`. One JSON object per line, `\n`-terminated.

**`PrettyConsoleFormatter`** (stream handler, TTY):

- Output: `{ts} [{level}] {logger}: {msg}  trace={trace_id_first8}  run={run_id_first8}  {category}` — coloured when supported; trace/run only when present.
- Used only when `sys.stderr.isatty()` and `DEBUG=1`; non-TTY runs (Docker, systemd) use JSON on stderr too so container logs stay machine-readable.

### Django `LOGGING` config wiring (`config/settings.py`)

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json":   {"()": "apps.observability.formatter.JsonLineFormatter"},
        "pretty": {"()": "apps.observability.formatter.PrettyConsoleFormatter"},
    },
    "handlers": {
        "events_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "events.jsonl",
            "maxBytes": OBSERVABILITY_EVENTS_MAX_BYTES,
            "backupCount": OBSERVABILITY_EVENTS_BACKUPS,
            "formatter": "json",
        },
        "heartbeat_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOGS_DIR / "heartbeats.jsonl",
            "maxBytes": OBSERVABILITY_HEARTBEATS_MAX_BYTES,
            "backupCount": OBSERVABILITY_HEARTBEATS_BACKUPS,
            "formatter": "json",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "pretty" if sys.stderr.isatty() and DEBUG else "json",
        },
    },
    "loggers": {
        "apps": {"handlers": ["events_file", "console"], "level": "INFO", "propagate": False},
        "apps.observability.heartbeat": {
            "handlers": ["heartbeat_file"], "level": "INFO", "propagate": False,
        },
    },
    "root": {"handlers": ["events_file", "console"], "level": "INFO"},
}
```

`apps.observability` is a hard dependency: if it can't import, Django fails to start.

## Heartbeats & freshness checker

### Helper (`apps/observability/heartbeat.py`)

```python
def emit_heartbeat(
    name: str,
    status: str = "ok",
    duration_ms: float | None = None,
    metrics: dict | None = None,
) -> None:
    """Write one heartbeat record. Never raises; falls back to logger.warning."""
    logger = logging.getLogger("apps.observability.heartbeat")
    logger.info("heartbeat", extra={
        "name": name, "status": status,
        "duration_ms": duration_ms, "metrics": metrics or {},
    })


@contextmanager
def heartbeat(name: str, **metrics):
    start = time.perf_counter()
    emit_heartbeat(name, status="running")
    try:
        yield
        emit_heartbeat(name, status="ok",
                       duration_ms=(time.perf_counter() - start) * 1000,
                       metrics=metrics)
    except Exception as exc:
        emit_heartbeat(name, status="fail",
                       duration_ms=(time.perf_counter() - start) * 1000,
                       metrics={"error_type": type(exc).__name__, **metrics})
        raise
```

### In-code registry (`apps/observability/heartbeat_registry.py`)

```python
HEARTBEAT_REGISTRY: dict[str, HeartbeatSpec] = {
    "check_health.hourly":  HeartbeatSpec(max_age=timedelta(minutes=75),  desc="Hourly health-check cron"),
    "check_health.daily":   HeartbeatSpec(max_age=timedelta(hours=25),    desc="Daily health-check cron"),
    "push_to_hub":          HeartbeatSpec(max_age=timedelta(minutes=15),  desc="Agent → hub alerts push", agent_only=True),
    "cluster_push.events":  HeartbeatSpec(max_age=timedelta(minutes=15),  desc="Agent → hub log push",    agent_only=True),
    "preflight.scheduled":  HeartbeatSpec(max_age=timedelta(hours=25),    desc="Daily preflight"),
}
```

Unregistered heartbeats are still written (forensics value) but produce no stale alerts. `agent_only` entries skipped in hub mode.

### Reader (`apps/observability/heartbeat_reader.py`)

`latest_heartbeats()` scans `heartbeats.jsonl` plus the most recent rotated backup, line-by-line, returns `{name: HeartbeatRecord}` with max-`ts` winning. Tolerates malformed lines (skip + warn, never raise).

### Freshness checker — three integration points

1. **Django system check** (`tag=heartbeat`):
   - `observability.H001` (warning) — heartbeat *name* is *age* old (max *max_age*).
   - `observability.H002` (warning) — heartbeat *name* has never been seen.
   - `observability.H003` (warning) — heartbeat *name* last status was `fail`.
2. **Standalone `manage.py check_heartbeats [--json]`** — for cron invocation and CI. Exit 0 on all-fresh, 1 on any stale.
3. **Alert emission** — for every stale `name`, the standalone command calls:

   ```python
   AlertOrchestrator().process_webhook(
       {
           "source": "observability",
           "fingerprint": f"heartbeat-stale:{name}",
           "title": f"Heartbeat stale: {name}",
           "severity": "warning",
           "labels": {"job": name, "max_age_seconds": ..., "last_seen": ...},
           "description": spec.desc,
       },
       driver="internal",
   )
   ```

   The fingerprint dedups so repeated staleness updates one `Incident` instead of creating new ones. `apps.notify` resolves channels via the existing `NotifySelector`; no new notification config in observability.

### Internal alert driver (`apps/alerts/drivers/internal.py`)

```python
class InternalDriver(BaseDriver):
    name = "internal"
    signature_header = None  # not webhook-reachable

    def validate(self, payload: dict) -> bool:
        return all(k in payload for k in ("source", "fingerprint", "title", "severity", "labels"))

    def parse(self, payload: dict) -> ParsedAlert:
        return ParsedAlert(
            source=payload["source"],
            fingerprint=payload["fingerprint"],
            title=payload["title"],
            severity=payload["severity"],
            labels=payload["labels"],
            description=payload.get("description", ""),
        )
```

Registered in `DRIVER_REGISTRY` for in-process lookup but **not** routed at `/alerts/webhook/`. Refuses signature/auth concerns by design (`signature_header = None`).

### Rollout — where heartbeats get added

| Job | Caller | Heartbeat key |
|---|---|---|
| Hourly health check | `manage.py check_health` (cron) | `check_health.hourly` |
| Daily health check | `manage.py check_health --all` (cron) | `check_health.daily` |
| Agent → hub alerts | `manage.py push_to_hub` | `push_to_hub` |
| Agent → hub logs | `manage.py push_logs_to_hub` (new) | `cluster_push.events` |
| Preflight | `manage.py preflight` (cron) | `preflight.scheduled` |

Each gets wrapped at the top of `handle()` in `with heartbeat("..."):`.

### Retirement of legacy cron checks

`checkers.W015` (cron.log staleness) and `checkers.W016` (cron.log size) are removed in this PR. Their replacements are `observability.H001`/`H002`.

## CLI reader

### Operator-facing surface (`bin/cli/logs.sh`)

```
$ cli logs                      # interactive submenu (matches cli health, cli notify, etc.)
$ cli logs view [filters]       # one-shot print
$ cli logs tail [filters]       # live follow
$ cli logs trace <trace_id>     # all records across both streams for one request
$ cli logs heartbeats           # latest per registered heartbeat, table view
```

Thin Bash wrapper that forwards to `manage.py read_logs ...`. Same pattern as `cli health → manage.py check_health`.

### Backing command: `manage.py read_logs`

```
manage.py read_logs view
  [--category {pipeline,http,cron,cluster,alerts,checkers,intelligence,notify,observability,internal}]
  [--level    {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
  [--logger   apps.notify.drivers.slack]
  [--trace-id <uuid>]
  [--run-id   <uuid>]
  [--incident <int>]
  [--since    "10m" | "2h" | "2026-05-17T12:00:00Z"]
  [--until    "..."]
  [--grep     "regex"]
  [--last     N]                   # default 200
  [--instance <name>]              # cluster mode; pivots to LOGS_DIR/cluster/<name>/
  [--stream   {events,heartbeats,all}]  # default: events
  [--json | --plain]               # default: pretty
  [--no-pager]                     # default: pipe to less -R on TTY
```

`tail` accepts the same flags plus `--from-end N`. `trace <id>` is shorthand for `view --trace-id <id> --stream all --last 1000`. `heartbeats` renders a table from `latest_heartbeats()`.

### Output formats

| Mode | Trigger | Use |
|---|---|---|
| Pretty (default on TTY) | auto | Coloured, aligned: `14:23:01  INFO  alerts.services  Created incident…  trace=7c3a…  run=4ef1…` |
| JSON | `--json` or non-TTY | One JSON object per line, identical to on-disk |
| Plain | `--plain` | Pretty without ANSI |

Pager: `less -R +G` on TTY unless `--no-pager`. Same convention as the rest of `bin/cli/`.

### Reading mechanics

- Single-host: opens `LOGS_DIR/events.jsonl` plus rotated backups (`events.jsonl.1`, `.2`, …) in chronological order. Streams line-by-line; never loads the whole file.
- `--last N`: reverse-reads from end-of-file (and rotated backups if needed) until N matching records found, then prints forward. Scan capped at `5 × rotation_max_bytes`.
- `tail` follow: inotify on Linux, polling on macOS (`stat.st_size` every 250 ms). Reopens on rotation (inode change).
- `--instance <name>` (hub mode): swaps source path to `LOGS_DIR/cluster/<name>/*.jsonl`. `--instance all` interleaves by `ts` across every per-instance directory.

### Permissions

Honours `LOGS_DIR` perms — `manage.py read_logs` runs as the application user; if the operator's shell user can't read the files, clear error message recommending `sudo -u <appuser>` or the `cli` wrapper. The `bin/cli/logs.sh` script auto-applies the same `sudo -u <appuser>` envelope used elsewhere.

## Cluster logbook

### Minimum-viable identity / storage

- **`APIKey.name` doubles as `instance_id`.** Admin creates an `APIKey` named `agent-eu-1` with `allowed_endpoints=["/cluster/logs/"]`. Hub treats `request.api_key.name` as the instance identifier — same field already used for rate-limit identity.
- **`APIKey.last_used_at` doubles as "last seen."** Updated for free by `APIKeyAuthMiddleware`. No separate state.
- **Per-instance storage**: `LOGS_DIR/cluster/<api_key.name>/{events,heartbeats}.jsonl`. `api_key.name` validated against `^[a-z0-9._-]{1,64}$` at write time.

No new model. No new admin section. No new schema.

### Agent: `manage.py push_logs_to_hub`

```
push_logs_to_hub
  [--stream {events,heartbeats,all}]      # default: all
  [--max-bytes-per-request N]             # default: 5 MiB
  [--max-bytes-per-run     N]             # default: 50 MiB (safety cap)
```

Cron-invoked (default every minute when `CLUSTER_ENABLED=1` and `HUB_URL` is set). Wrapped in `with heartbeat("cluster_push.events"):`.

Cursor state in `LOGS_DIR/cluster_push_cursor.json`:

```json
{
  "events":     {"inode": 8814217, "offset": 4823091},
  "heartbeats": {"inode": 8814532, "offset":    9277}
}
```

Per tick:

1. Open current `events.jsonl`. If inode differs from cursor, drain the rotated-out file first.
2. Read bytes from cursor `offset` up to `min(EOF, offset + max_bytes_per_request)`.
3. Truncate the chunk on the last newline so a partial JSON line never ships.
4. POST to hub. On `2xx` with `accepted_bytes`, advance cursor; persist cursor file atomically (write-then-rename).
5. Loop until EOF or `max_bytes_per_run` hit.

Failure modes:

| Failure | Behaviour |
|---|---|
| Hub unreachable / 5xx | Exit 1; cursor untouched; next tick retries from same offset. |
| Hub 4xx | Log redacted response body; emit `heartbeat("cluster_push.events", status="fail")`. Cursor untouched. Operator must investigate. |
| Local file disappeared | Reset cursor for that stream; warn; continue. |
| Backlog exceeds `max_bytes_per_run` | Next tick continues; freshness alert fires if behind for > `max_age`. |

### Hub endpoint: `POST /cluster/logs/<stream>/`

URL-routed at top of project: `path("cluster/", include("apps.observability.urls"))`.

```python
@method_decorator(csrf_exempt, name="dispatch")
class ClusterLogPushView(View):
    INSTANCE_NAME_RE = re.compile(r"^[a-z0-9._-]{1,64}$")

    def post(self, request, stream: str):
        if stream not in {"events", "heartbeats"}:
            return JsonResponse({"error": "unknown stream"}, status=404)
        name = getattr(getattr(request, "api_key", None), "name", "")
        if not self.INSTANCE_NAME_RE.fullmatch(name):
            return JsonResponse({"error": "api key name is not a valid instance id"}, status=403)

        body = request.body
        if len(body) > settings.OBSERVABILITY_CLUSTER_MAX_BODY_BYTES:
            return JsonResponse({"error": "body too large"}, status=413)
        try:
            _validate_jsonl(body)
        except _JsonlValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        target = settings.LOGS_DIR / "cluster" / name / f"{stream}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as f:
            f.write(body)
        return JsonResponse({"accepted_bytes": len(body)}, status=202)
```

- Auth via existing `APIKeyAuthMiddleware`; instance derived from `request.api_key.name`, never trusted from the body.
- Body must be well-formed JSON-lines; reject the whole request on the first malformed line (no half-accepted state).
- POSIX append-write atomic at our sizes.

### Cluster freshness check

One Django system check that iterates active `APIKey`s whose `allowed_endpoints` contain `/cluster/logs/`, warns on any whose `last_used_at` is older than `OBSERVABILITY_CLUSTER_MAX_AGE` (default 15 min). Surfaces in `preflight` and admin. Operators who want notifications run `manage.py check_cluster_freshness` from cron — same internal-driver-Alert path as Section 4. No new state.

### Role-agnostic by design — topology deferred

Nothing in this section assumes a single hub or single direction. A single host can be both an agent (it sets `HUB_URL` and runs `push_logs_to_hub`) and a hub (it has `/cluster/logs/` routed and serves it). Two hosts can push to each other if each holds an `APIKey` on the other. Cluster topology — fan-in vs mesh, agent-as-hub configurability, multi-hop forwarding, identity / discovery — is **a separate brainstorm**.

## Error handling, rotation, install integration

### Failure modes per component

| Component | Failure | Behaviour |
|---|---|---|
| `JsonLineFormatter` | object not JSON-serialisable | `json.dumps(..., default=str)` stringifies; line still written |
| `JsonLineFormatter` | formatter itself raises | `RotatingFileHandler.handleError` → stderr; application never sees logging exception |
| `emit_heartbeat()` | file write fails (disk full / perms) | Caught inside helper; falls back to `logger.warning(...)`; never raises |
| `latest_heartbeats()` | malformed JSON line | Skipped, counted, one warning per scan; returns what it could parse |
| `push_logs_to_hub` | hub unreachable / 5xx | Exit 1; cursor untouched |
| `push_logs_to_hub` | hub 4xx | Log redacted body; `heartbeat fail`; cursor untouched; manual investigation |
| `push_logs_to_hub` | local file disappeared | Reset cursor for that stream; warn |
| `ClusterLogPushView` | body not valid JSON-lines | 400; nothing appended |
| `ClusterLogPushView` | `api_key.name` regex fail | 403; no write |
| Internal alert driver | `AlertOrchestrator.process_webhook` raises | Caught by freshness checker, logged; `H001` still fires regardless (system check is the floor) |

### Rotation

| File | Handler | Default | Knob |
|---|---|---|---|
| `events.jsonl` | `RotatingFileHandler` | 50 MB × 5 backups | `OBSERVABILITY_EVENTS_MAX_BYTES`, `OBSERVABILITY_EVENTS_BACKUPS` |
| `heartbeats.jsonl` | `RotatingFileHandler` | 5 MB × 3 backups | `OBSERVABILITY_HEARTBEATS_MAX_BYTES`, `OBSERVABILITY_HEARTBEATS_BACKUPS` |
| Hub-side `cluster/<name>/*.jsonl` | external `logrotate` | weekly × 4, gzipped | shipped config in `bin/install/logrotate.d/server-monitoring.conf` |

### Settings (`config/settings.py` additions)

```python
# ------ Observability ------
OBSERVABILITY_EVENTS_MAX_BYTES       = int(os.environ.get("OBSERVABILITY_EVENTS_MAX_BYTES",       str(50 * 1024 * 1024)))
OBSERVABILITY_EVENTS_BACKUPS         = int(os.environ.get("OBSERVABILITY_EVENTS_BACKUPS",         "5"))
OBSERVABILITY_HEARTBEATS_MAX_BYTES   = int(os.environ.get("OBSERVABILITY_HEARTBEATS_MAX_BYTES",   str(5 * 1024 * 1024)))
OBSERVABILITY_HEARTBEATS_BACKUPS     = int(os.environ.get("OBSERVABILITY_HEARTBEATS_BACKUPS",     "3"))
OBSERVABILITY_CLUSTER_MAX_BODY_BYTES = int(os.environ.get("OBSERVABILITY_CLUSTER_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
OBSERVABILITY_CLUSTER_MAX_AGE        = int(os.environ.get("OBSERVABILITY_CLUSTER_MAX_AGE",        "900"))  # 15 min
```

One Django system check gates misconfiguration: `observability.W001` — `LOGS_DIR` not writable.

### Install integration

1. `bin/install/install.sh` adds two cron entries — guarded by an `is_observability_installed` check so re-runs are idempotent:

   ```
   * * * * *   <appuser>   cd <repo> && uv run manage.py push_logs_to_hub --quiet     # cluster mode only
   */5 * * * * <appuser>   cd <repo> && uv run manage.py check_heartbeats --quiet     # freshness, all modes
   ```

   The `push_logs_to_hub` entry is written only when `CLUSTER_ENABLED=1` and `HUB_URL` is set.

2. `bin/install/logrotate.d/server-monitoring.conf` ships a logrotate config for hub-side `LOGS_DIR/cluster/*/*.jsonl`. Install copies it into `/etc/logrotate.d/` if writable; otherwise prints the path and instructions.

3. `bin/cli/logs.sh` added to `bin/cli/cli.sh`'s menu loader.

4. `bin/check_security.sh` — no observability-specific row needed (observability is a hard dependency; if it can't load, Django won't start).

5. `apps.observability` appended to `INSTALLED_APPS`. No migration ships (no models in this design).

6. `apps/checkers/preflight/checks.py` gains a `check_observability_health` entry — checks `LOGS_DIR` writability, recent modification of `events.jsonl`, and registered-heartbeat freshness. Routed into the `crontab` tag group.

### Single-cut migration

System has never been deployed to production. No multi-step ramp. In one PR:

- Delete `LOGS_DIR/django.log` `FileHandler` from `LOGGING`.
- Delete `apps.checkers.management.commands.preflight.py`'s `CHECKS_LOG` write.
- Delete `checkers.W015` (cron.log staleness) and `checkers.W016` (cron.log size).
- New app shipped enabled; if its import fails, Django fails to start (correct failure mode for a logging system).

## Testing

### Per-module unit tests (`apps/observability/_tests/`)

| Test module | Covers |
|---|---|
| `_tests/test_context.py` | `bind()` / `snapshot()`; nested scopes; asyncio + thread isolation |
| `_tests/test_formatter.py` | round-trip parse via `json.loads`; non-serialisable → `default=str`; exception records; `extra={"category":"x"}` override; reserved-key strip; `instance_id` fallback |
| `_tests/test_heartbeat.py` | `emit_heartbeat()`; `heartbeat()` ctx mgr ok / fail / re-raise; disk-write failure swallowed |
| `_tests/test_heartbeat_reader.py` | reads live + `*.jsonl.1`; malformed line skipped; max-`ts` per `name`; empty file |
| `_tests/test_checks.py` | `H001` / `H002` / `H003`; `agent_only` skipped in hub mode |
| `_tests/test_internal_alert_driver.py` | required-field validation; `signature_header is None`; not exposed at `/alerts/webhook/` |
| `_tests/test_cluster_push.py` | cursor atomic write; rotation drain-then-switch; hub 4xx → cursor untouched + heartbeat fail; partial `accepted_bytes`; `max_bytes_per_run` cap; corrupt cursor → reset |
| `_tests/test_cluster_push_view.py` | unknown stream → 404; bad name → 403; body > cap → 413; malformed JSONL → 400 with no write; happy path → 202 + bytes appended |
| `_tests/test_freshness_alert_flow.py` | stale → `Incident` with `fingerprint=heartbeat-stale:<name>`; second tick updates same incident (dedup); pipeline error → `H001` still fires |
| `_tests/test_read_logs.py` | filter combinations; `--last N` reverse-scan cap; `--instance` swap; `--instance all` interleave; output formats |
| `_tests/test_logs_cli_wrapper.py` | exit codes propagate; `sudo -u <appuser>` envelope; flag passthrough |

### Hooks tested in their owning apps

- `config/_tests/test_observability_middleware.py` — HTTP middleware sets `trace_id`, restores on response, doesn't leak across requests.
- `config/_tests/test_celery_signals.py` — `task_prerun` / `task_postrun` set + clear.
- `apps/orchestration/_tests/test_orchestrator.py` — extends existing tests to assert `trace_id` / `run_id` / `incident_id` / `stage` appear in `events.jsonl` for a pipeline run.

### Integration / end-to-end

- `_tests/test_e2e_pipeline_logs.py` — `run_pipeline --sample` (sync), parse `events.jsonl`, assert every line for that `run_id` carries `trace_id` and `incident_id`, and `category` resolves correctly per stage.
- `_tests/test_e2e_heartbeat_to_alert.py` — register a `HeartbeatSpec` with `max_age=0.1s`, sleep, run `manage.py check_heartbeats`, assert `Incident(fingerprint="heartbeat-stale:<name>")` exists.
- `_tests/test_e2e_cluster_roundtrip.py` — agent writes lines to `events.jsonl`, runs `push_logs_to_hub` against a Django test client acting as the hub, assert hub-side `LOGS_DIR/cluster/<name>/events.jsonl` matches byte-for-byte; cursor advances; second run pushes nothing.

### Coverage rules

Per project convention: 100% branch coverage. Easy-to-miss branches:

- Cursor-rotation edge case in `push_logs_to_hub` (inode changed mid-run).
- `JsonLineFormatter` exception-record path (`exc_info` present vs absent).
- `latest_heartbeats()` reading rotated backup when live file is empty.
- `ClusterLogPushView` partial-JSONL validation (one bad line → reject whole body).
- `H003` "last status fail" path when heartbeat is fresh but `status="fail"`.

Run: `uv run coverage run -m pytest && uv run coverage report`.

### Static checks

New observability code goes through ruff, black, mypy as standard.

Parking-lot rule (not required to ship): a `flake8-tidy-imports` ban on `logging.FileHandler` outside `apps/observability/` would prevent ad-hoc file handlers springing up elsewhere.

## Open questions / parking lot

- **Cluster topology** — separate brainstorm. Today's design is role-agnostic at the storage / transport layer; the topology brainstorm decides who pushes to whom, how nodes discover each other, what authority model gates "hub of hubs."
- **Future CLI knobs** — `--export <path>` (write filtered output as JSONL to a file) and `--summary` (group-by category count). Designed for, not built yet.
- **Ruff rule** — ban `logging.FileHandler` outside `apps/observability/` to prevent ad-hoc handlers.
- **Hub-side metrics** — out of scope. This is a log stack, not a metrics stack.

## Cross-references

- ISO 27003 audit ([`2026-05-12-iso-27003-security-audit-notes.md`](2026-05-12-iso-27003-security-audit-notes.md)) — the trust-boundary discipline this design respects (no notification path inside observability; internal driver is in-process only; cluster identity from `APIKey`, not body).
- SSTI protection ([`2026-04-15-ssti-notify-template-design.md`](2026-04-15-ssti-notify-template-design.md)) — notification templates the freshness alert renders through.
- Path-traversal prevention ([`2026-04-11-path-traversal-prevention-design.md`](2026-04-11-path-traversal-prevention-design.md)) — `INSTANCE_NAME_RE` regex on the cluster push endpoint mirrors the same allowlist-by-construction pattern.