---
title: "System Status Command Design"
parent: Plans
---

# System Status Command Design

## Purpose

A new `system_status` management command that gives operators a single-glance view of system configuration and flags inconsistencies across all config sources (.env, .env.sample, settings.py, database state, filesystem, shell scripts).

Separate from `preflight` (which checks health), this checks **configuration coherence**.

## Command Interface

```
python manage.py system_status                # Dashboard + issues
python manage.py system_status --json         # Full JSON for CI
python manage.py system_status --checks-only  # Skip dashboard, issues only
python manage.py system_status --verbose      # Include passing checks
```

Integration: `bin/cli.sh status` subcommand.

## Dashboard Output

### System Profile

```
═══ System Profile ══════════════════════════
  Role:        agent → hub at https://monitoring-hub.example.com
  Environment: production (DEBUG=off)
  Deploy:      bare (systemd)
  Database:    SQLite @ /var/lib/sm/db.sqlite3
  Celery:      redis://localhost:6379/0 (eager=off)
  Metrics:     statsd (localhost:8125, prefix=pipeline)
  Logging:     /var/log/sm/
  Instance ID: node-west-1
```

Role derivation:
- `HUB_URL` set, `CLUSTER_ENABLED=0` → `agent → hub at <url>`
- `CLUSTER_ENABLED=1`, no `HUB_URL` → `hub (accepting cluster payloads)`
- Neither → `standalone`
- Both → conflict (flagged as error)

### Pipeline State

```
═══ Pipeline State ══════════════════════════
  Channels:    slack (active), email (inactive)
  Intelligence: local (active)
  Last run:    2026-04-05 08:12 UTC — notified (OK)
```

### Pipeline Definitions

```
═══ Pipeline Definitions ════════════════════
  full-pipeline (active)
    alerts: webhook → checkers: cpu,memory,disk → intelligence: local → notify: slack
  health-only (active)
    checkers: cpu,memory,disk,network → notify: email
  legacy-monitor (inactive)
    alerts: webhook → checkers: cpu → notify: email
```

Each definition shows its stage chain with configured drivers/checkers/providers/channels. Inactive definitions are dimmed in terminal output.

## Consistency Checks

### Env File Consistency

| Check | Sources | Severity |
|-------|---------|----------|
| Keys in `.env.sample` missing from `.env` | `.env` vs `.env.sample` | WARN |
| Keys in `.env` not in `.env.sample` (unknown) | `.env` vs `.env.sample` | WARN |
| Keys in `settings.py` missing from `.env.sample` | `settings.py` vs `.env.sample` | WARN |
| Keys in `.env.sample` never referenced in code | `.env.sample` vs `settings.py` + `bin/` | WARN |
| Commented-out sample keys that are set in `.env` | `.env` vs `.env.sample` | INFO |

### Cluster Profile Coherence

| Check | Logic | Severity |
|-------|-------|----------|
| Agent+hub conflict | `HUB_URL` set AND `CLUSTER_ENABLED=1` | ERROR |
| Agent without secret | `HUB_URL` set, `WEBHOOK_SECRET_CLUSTER` empty | WARN |
| Agent without instance ID | `HUB_URL` set, `INSTANCE_ID` empty | WARN |
| Hub without secret | `CLUSTER_ENABLED=1`, `WEBHOOK_SECRET_CLUSTER` empty | ERROR |

### Environment vs Runtime State

| Check | Logic | Severity |
|-------|-------|----------|
| Debug on in production | `DJANGO_ENV=prod`, `DJANGO_DEBUG=1` | ERROR |
| No allowed hosts in production | `DJANGO_ENV=prod`, `DJANGO_ALLOWED_HOSTS` empty | ERROR |
| Celery eager in production | `DJANGO_ENV=prod`, `CELERY_TASK_ALWAYS_EAGER=1` | WARN |
| StatsD configured but backend=logging | `STATSD_HOST` set, `METRICS_BACKEND=logging` | INFO |
| Metrics backend=statsd but no host | Reverse | WARN |

### Database vs Config State

| Check | Logic | Severity |
|-------|-------|----------|
| Active pipelines but Celery eager | Active `PipelineDefinition` + eager mode | WARN |
| Notification channels without credentials | Active channel missing webhook/config in DB | WARN |
| No active notification channels | Zero active channels | WARN |
| No active pipeline definitions | Zero active definitions | INFO |
| Intelligence provider active, fallback disabled | Active provider + `FALLBACK_ENABLED=0` | INFO |

### Installation State

| Check | Logic | Severity |
|-------|-------|----------|
| Aliases not installed (dev) | `DJANGO_ENV=dev`, `bin/aliases.sh` missing | WARN |
| Pre-commit hooks not installed (dev) | `DJANGO_ENV=dev`, `.git/hooks/pre-commit` missing | WARN |
| Cron not configured (prod) | `DJANGO_ENV=prod`, no crontab entries | WARN |
| Logs directory not writable | `LOGS_DIR` not writable | ERROR |
| Database file not writable | SQLite path not writable | ERROR |

## Architecture

### File Layout

```
apps/checkers/management/commands/system_status.py   # Command
apps/checkers/status/                                 # Status modules
    __init__.py
    dashboard.py                                      # Profile dashboard renderer
    env_checks.py                                     # .env vs .env.sample vs settings.py
    cluster_checks.py                                 # Cluster profile coherence
    runtime_checks.py                                 # Environment vs runtime state
    database_checks.py                                # DB state vs config
    installation_checks.py                            # Installation state checks
```

### Why Not Django System Checks?

The consistency checks compare **across sources** (env vs sample vs settings vs database vs filesystem). Django's `@register()` framework is designed for single-source validation. These checks need to:

- Parse `.env.sample` as a raw file
- Scan `settings.py` source for `os.environ.get` references
- Compare key sets across multiple files

### CheckResult Dataclass

```python
@dataclass
class CheckResult:
    level: str          # "ok", "info", "warn", "error"
    message: str
    hint: str = ""
    category: str = ""  # "env", "cluster", "runtime", "database", "installation"
```

Each module exposes `run() -> list[CheckResult]`.

### JSON Output

```json
{
  "profile": {
    "role": "agent",
    "hub_url": "https://monitoring-hub.example.com",
    "environment": "production",
    "debug": false,
    "deploy_method": "bare",
    "database": "sqlite:///var/lib/sm/db.sqlite3",
    "celery_broker": "redis://localhost:6379/0",
    "celery_eager": false,
    "metrics_backend": "statsd",
    "instance_id": "node-west-1",
    "logs_dir": "/var/log/sm/"
  },
  "pipeline": {
    "channels": [{"name": "slack", "active": true}],
    "intelligence": [{"name": "local", "active": true}],
    "last_run": {"timestamp": "2026-04-05T08:12:00Z", "status": "notified"}
  },
  "definitions": [
    {
      "name": "full-pipeline",
      "active": true,
      "stages": [
        {"stage": "alerts", "drivers": ["webhook"]},
        {"stage": "checkers", "checkers": ["cpu", "memory", "disk"]},
        {"stage": "intelligence", "providers": ["local"]},
        {"stage": "notify", "channels": ["slack"]}
      ]
    }
  ],
  "checks": [
    {"level": "error", "category": "cluster", "message": "...", "hint": "..."}
  ],
  "summary": {"passed": 12, "warnings": 2, "errors": 1}
}
```

## Testing

- Each status module: `apps/checkers/_tests/status/test_<module>.py`
- Mock `.env` / `.env.sample` content and Django settings
- Dashboard rendering tested with known config states
- JSON output tested for schema correctness
- 100% branch coverage required

## CLI Integration

Add `status` subcommand to `bin/cli.sh` alongside existing `health`.