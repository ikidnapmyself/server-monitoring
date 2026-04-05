---
title: "2026-04-05 Logging and Update History Design"
parent: Plans
---

{% raw %}

# Centralized Logging and Update History

## Problem

Logs are scattered across the project root (`update.log`, `cron.log`, `push.log`). No Django logging config exists. No structured update history — just flat text logs. No way to see past updates at a glance.

## Goals

1. **Centralized `logs/` directory** — all logs in one gitignored location
2. **Django LOGGING config** — file + console handlers for Python-side logging
3. **Shell scripts use `logs/`** — update, cron, push logs migrate to `logs/`
4. **Update history** — structured JSONL file with `--history` flag to view
5. **Documentation** — `docs/Logging.md` covering log layout, debugging, rotation

## Design

### Directory Structure

```
logs/                          # gitignored, created by bin/lib/paths.sh
├── django.log                 # Django app logs (Python logging)
├── update.log                 # update.sh output
├── update-history.jsonl       # structured update history (one JSON per run)
├── cron.log                   # cron health check output
└── push.log                   # cluster push output
```

### Django LOGGING (`config/settings.py`)

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "file": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "django.log",
            "formatter": "verbose",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "apps": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
```

Ensure `logs/` directory exists before Django starts (in settings or via `bin/lib/paths.sh`).

### Shell Log Path Migration

Add to `bin/lib/paths.sh`:
```bash
export LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
```

Update references:
- `bin/lib/update.sh`: `_up_log_file` → `$LOG_DIR/update.log`
- `bin/install/cron.sh`: cron commands use `$LOG_DIR/cron.log`, `$LOG_DIR/push.log`

### Update History (`logs/update-history.jsonl`)

One JSON line per update run, appended by `run_update()`:

```json
{"timestamp":"2026-04-05T10:30:00+0200","old_sha":"abc1234","new_sha":"def5678","status":"success","commits":3,"mode":"dev","auto_update":false,"failed_step":null,"rolled_back":false}
```

Fields:
- `timestamp` — ISO 8601
- `old_sha`, `new_sha` — 7-char short SHAs
- `status` — `success`, `failed`, `up_to_date`
- `commits` — number of commits pulled
- `mode` — dev/prod/docker/systemd
- `auto_update` — true if invoked with `--auto-env` (cron context)
- `failed_step` — which step failed (null on success)
- `rolled_back` — whether rollback was performed

Written by a new `_up_record_history()` function called at the end of `run_update()`.

### CLI: `update.sh --history`

```
Update History (last 20):
DATE                 FROM     TO       STATUS      COMMITS  MODE    AUTO
2026-04-05 10:30     abc1234  def5678  success     3        dev     no
2026-04-05 09:00     abc1234  abc1234  up_to_date  0        dev     yes
2026-04-04 22:15     9a8b7c6  abc1234  failed      2        prod    yes (rolled back)
```

Flags:
- `--history` — show last 20 entries as table
- `--history --json` — dump raw JSONL
- `--history -n 5` — limit to last N entries

### Gitignore

Add `logs/` to `.gitignore`.

### Documentation: `docs/Logging.md`

Covers:
- Log directory layout and what each file contains
- Django logging config (adjusting levels, adding handlers)
- Shell script logs (update, cron, push)
- Update history (--history usage, JSONL format)
- Debugging tips (tail -f, grep patterns, DEBUG level)
- Log rotation recommendations

{% endraw %}