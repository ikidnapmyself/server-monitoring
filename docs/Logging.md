---
title: Logging
layout: default
nav_order: 6
---

# Logging

## Overview

All logs live in the `logs/` directory at the project root. This directory is
gitignored and created automatically when Django starts (via `LOGS_DIR.mkdir(exist_ok=True)`
in settings).

Django app logs go through Python's standard `logging` module and are written to
`logs/django.log`. Shell scripts (`bin/update.sh`, cron jobs) write directly to
their respective log files in the same directory.

## Log Directory Layout

| File | Source | Contents |
|------|--------|----------|
| `logs/django.log` | Django (Python logging) | App logs from all `apps.*` loggers |
| `logs/update.log` | `bin/update.sh` | Update script output |
| `logs/update-history.jsonl` | `bin/update.sh` | Structured update history (JSON lines) |
| `logs/cron.log` | cron job | Health check pipeline output |
| `logs/push.log` | cron job | Cluster push-to-hub output |

## Django Logging

The logging configuration lives in `config/settings.py` under the `LOGGING` dict.
It defines two handlers:

- **file** -- writes to `logs/django.log` using the `verbose` formatter
- **console** -- writes to stderr using the same formatter

The `verbose` format produces lines like:

```
2026-04-04 12:34:56,789 [INFO] apps.alerts.drivers.grafana: Parsed 3 alerts from webhook
```

The root logger and the `apps` logger both use level `INFO` by default.

### Changing the log level

Edit `config/settings.py` and change the `level` value in the relevant logger:

```python
"loggers": {
    "apps": {
        "handlers": ["console", "file"],
        "level": "DEBUG",       # was "INFO"
        "propagate": False,
    },
},
```

### Adding a logger for a specific app

Add a new entry under `loggers` in the `LOGGING` dict:

```python
"loggers": {
    "apps": { ... },
    "apps.intelligence": {
        "handlers": ["console", "file"],
        "level": "DEBUG",
        "propagate": False,
    },
},
```

Then use it in your code:

```python
import logging
logger = logging.getLogger("apps.intelligence")
logger.debug("Provider returned %d tokens", token_count)
```

## Update History

`bin/update.sh` records every update attempt as a JSON line in
`logs/update-history.jsonl`. Use the `--history` flag to view it:

```bash
bin/update.sh --history           # last 20 updates
bin/update.sh --history -n 5      # last 5
bin/update.sh --history --json    # raw JSONL
```

The formatted output looks like:

```
Update History (last 3):
DATE                 FROM      TO        STATUS       COMMITS  MODE     AUTO
--------------------------------------------------------------------------------
2026-04-03 09:15:22  a1b2c3d   e4f5g6h   success      4        venv     no
2026-04-02 14:30:01  e4f5g6h   i7j8k9l   failed       0        venv     yes
2026-04-01 08:00:00  i7j8k9l   i7j8k9l   up_to_date   0        venv     no
```

### JSONL field reference

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | string | ISO 8601 timestamp of the update attempt |
| `old_sha` | string | Short SHA before the update |
| `new_sha` | string | Short SHA after the update |
| `status` | string | `success`, `failed`, or `up_to_date` |
| `commits` | number | Number of commits pulled |
| `mode` | string | Python environment mode (`venv`, `system`, etc.) |
| `auto_update` | boolean | Whether `--auto-env` was used |
| `failed_step` | string or null | Which step failed (e.g., `pull`, `migrate`) |
| `rolled_back` | boolean | Whether a rollback was performed |

## Debugging

Common commands for tailing and searching logs:

```bash
tail -f logs/django.log           # follow Django logs
tail -f logs/update.log           # follow update output
tail -f logs/cron.log             # follow cron output
grep ERROR logs/django.log        # find errors
grep '"status":"failed"' logs/update-history.jsonl  # find failed updates
```

## Log Rotation

For production deployments, configure `logrotate` to prevent log files from
growing indefinitely:

```
/path/to/project/logs/*.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

Save this as `/etc/logrotate.d/server-maintanence` and logrotate will handle
rotation automatically on its next run.