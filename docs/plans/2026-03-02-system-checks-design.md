---
title: "System Checks Expansion — Design"
parent: Plans
nav_exclude: true
---
# System Checks Expansion — Design

## Problem

The project has two check systems — Django system checks (`checks.py`) and health checkers
(`checkers/`) — but only covers database, migrations, crontab, and aliases. Missing coverage
for debug mode, environment validation, pipeline/instance status, notification channels,
cron log health, and other operational concerns. No unified CLI entry point.

## Decision

**Approach C: bin script + management command + expanded checks.py**

Three layers, clean separation of concerns:

1. **`apps/checkers/checks.py`** — Django system checks (Python-level, post-boot validation)
2. **`manage.py preflight`** — Management command that runs all Django checks with formatted output
3. **`bin/check_system.sh`** — Shell-level pre-Django checks, then delegates to `preflight`

No duplication — `preflight` is a consumer of `checks.py`, not a reimplementation.

## Layer 1: Django System Checks (`checks.py`)

### Existing checks (unchanged)

| ID | Tag | Description |
|---|---|---|
| `checkers.E001` | `database` | Database connectivity |
| `checkers.W001` | `migrations` | Pending migrations |
| `checkers.W002-W008` | `crontab` | Cron job configuration |
| `checkers.W009` | `aliases` | Shell aliases (dev only) |
| `checkers.E003-E004` | `database` (deploy) | Database tables exist |

### New checks

| ID | Tag | Severity | Description |
|---|---|---|---|
| `checkers.W010` | `security` | Warning | `DEBUG=True` in non-test, non-DEBUG env |
| `checkers.W011` | `security` | Warning | `SECRET_KEY` is weak (< 50 chars or contains "insecure") |
| `checkers.W012` | `environment` | Warning | `.env` file missing from `BASE_DIR` |
| `checkers.I003` | `environment` | Info | Required env vars from `.env.sample` not set (one per var) |
| `checkers.I001` | `pipeline` | Info | Pipeline definition counts (active/inactive) with names |
| `checkers.W014` | `pipeline` | Warning | Notification channels: zero active, or active with empty config |
| `checkers.W015` | `crontab` | Warning | `cron.log` stale (last modified > 1 hour ago, if cron is configured) |
| `checkers.W016` | `crontab` | Warning | `cron.log` too large (> 50MB, suggests no log rotation) |
| `checkers.W017` | `environment` | Warning | `BASE_DIR` not writable (can't write cron.log) |

### Tag usage

```bash
manage.py check --tag security      # W010, W011
manage.py check --tag environment   # W012, I003, W017
manage.py check --tag pipeline      # I001, W014
manage.py check --tag crontab       # W002-W008, W015, W016
manage.py check --tag migrations    # W001
manage.py check --tag database      # E001, E003-E004
manage.py check --tag aliases       # W009
manage.py check                     # All
```

### Check behaviors

**`check_debug_mode` (W010)**
- Skip in tests (`_is_testing()`)
- Skip if `DEBUG=False`
- Warning when `DEBUG=True` — hint: "Set DEBUG=False in production"

**`check_secret_key_strength` (W011)**
- Skip in tests
- Warning if `len(SECRET_KEY) < 50` or `"insecure"` in SECRET_KEY.lower()
- Hint: "Generate a strong secret key with django.core.management.utils.get_random_secret_key()"

**`check_env_file_exists` (W012)**
- Check `os.path.isfile(BASE_DIR / ".env")`
- Hint: "Copy .env.sample to .env and configure"

**`check_required_env_vars` (I003)**
- Parse `.env.sample` for variable names (lines matching `^[A-Z_]+=`)
- Check each against `os.environ`
- One Info per missing var
- Skip vars with `# optional` comment

**`check_pipeline_status` (I001)**
- Query `PipelineDefinition.objects.all()`
- Info message: "N pipeline definitions (X active, Y inactive)"
- List names with status

**`check_notification_channels` (W014)**
- Query `NotificationChannel.objects.filter(is_active=True)`
- Warning if zero active channels
- Warning for each active channel with empty/null config

**`check_cron_log_freshness` (W015)**
- Only check if cron is configured (reuse `check_crontab_configuration` logic)
- Check `os.path.getmtime(BASE_DIR / "cron.log")`
- Warning if > 1 hour since last modification
- Skip if `cron.log` doesn't exist (separate concern)

**`check_cron_log_size` (W016)**
- Check `os.path.getsize(BASE_DIR / "cron.log")`
- Warning if > 50MB
- Hint: "Consider log rotation (logrotate or truncate)"

**`check_base_dir_writable` (W017)**
- `os.access(BASE_DIR, os.W_OK)`
- Warning if not writable
- Hint: "Cron logs and other output require write access"

## Layer 2: Management Command (`manage.py preflight`)

**File**: `apps/checkers/management/commands/preflight.py`

### Interface

```bash
manage.py preflight                    # All checks, human output
manage.py preflight --only security    # Filter by tag(s)
manage.py preflight --only security,environment
manage.py preflight --json             # JSON output for CI
```

### Behavior

1. Collect all check tags (security, environment, pipeline, crontab, migrations, database, aliases)
2. If `--only` provided, filter to specified tags
3. For each tag group, run `django.core.checks.run_checks(tags=[tag])`
4. Format output with color and grouping:

```
=== Preflight Check ===

Security
  OK   Debug mode is OFF
  WARN Secret key appears weak (32 chars, recommend 50+)

Environment
  OK   .env file found
  WARN Missing env var: OPENAI_API_KEY (from .env.sample)
  OK   Project directory is writable

Pipeline
  INFO 3 pipeline definitions (2 active, 1 inactive)
         - local-smart (active)
         - full (active)
         - ai-analyzed (inactive)
  OK   2 active notification channels

Crontab
  OK   Cron job configured
  OK   cron.log last updated 3 min ago
  WARN cron.log is 52MB (consider log rotation)

Database
  OK   Connected to default
  OK   No pending migrations

Summary: 8 passed, 2 warnings, 0 errors
```

5. Exit code: 0 if no errors, 1 if any errors

### JSON output

```json
{
  "groups": {
    "security": {"checks": [...], "errors": 0, "warnings": 1},
    "environment": {"checks": [...], "errors": 0, "warnings": 1}
  },
  "summary": {"passed": 8, "warnings": 2, "errors": 0}
}
```

## Layer 3: CLI Script (`bin/check_system.sh`)

Shell-level checks that run **before** Django boots:

| Check | What it validates |
|---|---|
| `uv` installed | `command -v uv` |
| Python version | `python3 --version` is 3.10+ |
| `.env` exists | `test -f .env` |
| `.venv` exists | `test -d .venv` (dependencies installed) |
| `cron.log` writable | `touch` test |
| Disk space | `df` — project dir has >1GB free |

After shell checks, runs:
```bash
uv run python manage.py preflight
```

### Interface

```bash
bin/check_system.sh              # Full check (shell + Django)
bin/check_system.sh --shell-only # Only shell checks
bin/check_system.sh --django-only # Only manage.py preflight
```

## Testing

| Area | File | Tests |
|---|---|---|
| New Django checks | `apps/checkers/_tests/test_checks.py` | Each check function: happy path, warning triggered, error path |
| Preflight command | `apps/checkers/_tests/test_commands.py` | Output formatting, --only filter, --json, exit codes |
| Existing checks | No changes | Existing tests unchanged |

Shell script (`bin/check_system.sh`) is not unit-tested (consistent with other bin scripts).

## Documentation Updates

| File | Changes |
|---|---|
| `apps/checkers/README.md` | Add system checks section, preflight command, tag reference |
| `docs/Setup-Guide.md` | Add "Verifying Your Setup" section referencing `bin/check_system.sh` |
| `apps/checkers/agents.md` | Add preflight command contract |
| `CLAUDE.md` | Add `preflight` to essential commands |