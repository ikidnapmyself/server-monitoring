---
title: "Unified Preflight Design"
parent: Plans
---

# Unified Preflight Design

## Purpose

Consolidate 5 overlapping check entry points (`check_system.sh`, `check_security.sh`, `preflight`, `system_status`, Django system checks) into one command: `manage.py preflight`. One command, one output, everything visible.

## The Problem

The same check (e.g., DEBUG in prod) runs in 4 different places with different severity levels. Users don't know which command to run. None of them log results.

## Command Interface

```
manage.py preflight          # Run it. See everything.
manage.py preflight --json   # For scripts/CI
```

That's it. `--verbosity 0|1|2` stays because Django gives it for free.

## Output

```
═══ System ══════════════════════════════════
  Role:          standalone
  Environment:   dev (DEBUG=on)
  Deploy:        bare
  Database:      SQLite @ db.sqlite3
  Celery:        redis://localhost:6379/0 (eager)

═══ Checks ══════════════════════════════════
  OK   Python 3.12 (.venv)
  OK   uv installed
  OK   .env file found
  OK   Database connection works
  OK   No pending migrations
  WARN .env has keys not in .env.sample: EXTRA_VAR
  OK   DEBUG mode appropriate for dev
  OK   SECRET_KEY is 64 chars
  OK   Cluster: standalone (no conflicts)
  WARN Pre-commit hooks not installed
  OK   Logs directory writable

  11 checks: 9 passed, 2 warnings, 0 errors
```

No sections. No grouping. Flat list. Dashboard header shows system profile at a glance.

## Check Inventory (28 checks, in order)

### Environment

1. Python version >= 3.10, warn if not in project `.venv` (detects system Python)
2. `uv` installed
3. `.venv` directory exists
4. `.env` file exists
5. Project directory writable
6. Disk space > 1GB free

### Database

7. Database connection works
8. No pending migrations

### Security

9. DEBUG mode appropriate for environment (error if on in prod)
10. SECRET_KEY >= 50 chars
11. ALLOWED_HOSTS not empty in production
12. `.env` file not world-readable

### Config Consistency

13. `.env` keys vs `.env.sample` drift (missing/extra)
14. `settings.py` env refs vs `.env.sample` (undocumented vars)
15. Cluster role coherence (agent/hub/standalone conflicts)
16. Celery eager mode in production
17. Metrics backend vs StatsD config

### Pipeline State

18. Active notification channels exist
19. Active pipeline definitions (info if none)
20. Intelligence provider + fallback config

### Installation State

21. Shell aliases installed (dev)
22. Pre-commit hooks installed (dev)
23. Cron configured (prod)
24. Logs directory writable
25. Database file writable (SQLite)

### Deployment (conditional)

26. Docker containers running (docker mode only)
27. systemd services active (systemd mode only)
28. Gunicorn socket exists (systemd mode only)

## Python Version Check

Detects system Python vs project `.venv`:

```python
import sys

in_venv = sys.prefix != sys.base_prefix
venv_is_project = ".venv" in sys.prefix

if version < (3, 10):
    error("Python X.Y (need >= 3.10)")
elif not in_venv:
    warn("Running outside virtualenv", hint="Run via: uv run python manage.py preflight")
elif not venv_is_project:
    warn("Not using project .venv", hint="Activate project venv or run via: uv run")
else:
    ok("Python X.Y (.venv)")
```

## Architecture

### File Layout

```
apps/checkers/preflight/                        # New package
    __init__.py                                 # CheckResult dataclass
    dashboard.py                                # Profile + definitions renderer
    checks.py                                   # All 28 check functions, flat
    logger.py                                   # JSON-line logger to logs/checks.log

apps/checkers/management/commands/preflight.py  # Rewritten command
apps/checkers/_tests/preflight/                 # Tests mirror source
```

### What Gets Deleted

- `apps/checkers/status/` — entire package (absorbed into `preflight/`)
- `apps/checkers/management/commands/system_status.py` — absorbed
- `bin/lib/health_check.sh` — logic moves to Django
- `bin/lib/security_check.sh` — logic moves to Django

### What Gets Kept

- `apps/checkers/checks.py` — Django `@register()` checks stay for `manage.py check`
- `apps/checkers/management/commands/check_health.py` — runtime metrics, separate concern
- `bin/cli/health.sh` — unchanged

### Shell Script Wrappers

`bin/check_system.sh` and `bin/check_security.sh` become thin wrappers:

```bash
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
uv run python manage.py preflight "$@"
```

### CLI Menu Simplification

`bin/cli/system.sh` becomes:

```
═══ System & Security ═══
1) Run preflight checks
2) Set production mode
3) Back to main menu
```

## Logging

Every `preflight` run appends one JSON line to `logs/checks.log`:

```json
{"timestamp": "2026-04-05T14:30:00Z", "passed": 23, "warnings": 2, "errors": 0, "checks": [{"level": "ok", "message": "Python 3.12 (.venv)"}, ...]}
```

Readable via `tail -1 logs/checks.log | python -m json.tool`.

## JSON Output

`--json` produces the same structure as the log line, plus the dashboard data:

```json
{
  "profile": {
    "role": "standalone",
    "environment": "dev",
    "debug": true,
    "deploy_method": "bare",
    "database": "db.sqlite3",
    "celery_broker": "redis://localhost:6379/0",
    "celery_eager": true,
    "metrics_backend": "logging",
    "instance_id": "",
    "logs_dir": "logs/"
  },
  "definitions": [...],
  "checks": [
    {"level": "ok", "message": "Python 3.12 (.venv)"},
    {"level": "warn", "message": "Pre-commit hooks not installed", "hint": "Run: uv run pre-commit install"}
  ],
  "summary": {"passed": 9, "warnings": 2, "errors": 0}
}
```

## Testing

- Each check function tested individually in `apps/checkers/_tests/preflight/`
- Command integration tests for human and JSON output
- Logger tests for JSON-line format
- 100% branch coverage required