# Checkers

This Django app provides a small health-check framework and two management commands you can run locally or in automation.

> **Note:** For development setup (formatting, linting, testing), see the main [README](../../README.md#development).

## What's included

### Available checkers

These checkers are registered in `apps/checkers/checkers/__init__.py` (`CHECKER_REGISTRY`):

- `cpu` — CPU usage % (warn ≥ 70, critical ≥ 90)
- `memory` — RAM usage % (warn ≥ 70, critical ≥ 90)
- `disk` — disk usage % by path (warn ≥ 80, critical ≥ 95)
- `network` — % of hosts reachable via ping (OK ≥ 70%, warning ≥ 50%, else critical)
- `process` — % of named processes running (OK = 100%, warning ≥ 50%, else critical)

List them via:

```bash
uv run python manage.py check_health --list
```

### Django System Checks

In addition to runtime health checkers, this app registers Django system checks that run with `python manage.py check`. These verify project configuration:

| Tag | Check ID | Description |
|-----|----------|-------------|
| `database` | `checkers.E001` | Database connection error |
| `migrations` | `checkers.W001` | Pending migrations warning |
| `crontab` | `checkers.W002` | No crontab configured |
| `crontab` | `checkers.W004` | Health check cron job not found |
| `database` | `checkers.E003` | Missing django_migrations table (deploy) |

Run all system checks:

```bash
uv run python manage.py check
```

Run specific check tags:

```bash
uv run python manage.py check --tag database
uv run python manage.py check --tag migrations
uv run python manage.py check --tag crontab
```

Run deployment checks (includes additional security/config checks):

```bash
uv run python manage.py check --deploy
```

## Running checks

There are two management commands:

- `check_health` — run **all** checkers (or a selection) and show a summary
- `run_check` — run **one** checker with checker-specific options

### `check_health`

Run all checks:

```bash
uv run python manage.py check_health
```

Run only some checkers:

```bash
uv run python manage.py check_health cpu memory disk
```

JSON output:

```bash
uv run python manage.py check_health --json
```

#### Exit codes (automation)

By default, the command exits non-zero when:

- `2` if any check is **CRITICAL**
- `1` if any check is **UNKNOWN**
- `0` otherwise

To make it stricter:

```bash
# Exit 1 if any check is WARNING or CRITICAL
uv run python manage.py check_health --fail-on-warning

# Exit 1 only if any check is CRITICAL
uv run python manage.py check_health --fail-on-critical
```

#### Threshold overrides

Override thresholds for all checks in this run:

```bash
uv run python manage.py check_health --warning-threshold 75 --critical-threshold 92
```

#### Checker-specific options

```bash
# Disk paths
uv run python manage.py check_health disk --disk-paths / /System/Volumes/Data

# Ping targets
uv run python manage.py check_health network --ping-hosts 8.8.8.8 1.1.1.1 github.com

# Required processes
uv run python manage.py check_health process --processes nginx postgres
```

### `run_check`

Run a single check:

```bash
uv run python manage.py run_check cpu
```

Single-check JSON output:

```bash
uv run python manage.py run_check memory --json
```

#### CPU checker options

- `--interval` (seconds; default 1.0)
- `--per-cpu` (use the busiest core for the status)

```bash
uv run python manage.py run_check cpu --interval 0.5 --per-cpu
```

#### Memory checker options

- `--include-swap`

```bash
uv run python manage.py run_check memory --include-swap
```

#### Disk checker options

- `--paths` (one or more paths; default `/`)

```bash
uv run python manage.py run_check disk --paths / /System/Volumes/Data
```

#### Network checker options

- `--hosts` (one or more; default `8.8.8.8 1.1.1.1`)

```bash
uv run python manage.py run_check network --hosts 8.8.8.8 1.1.1.1 github.com
```

#### Process checker options

- `--names` (one or more process names)

```bash
uv run python manage.py run_check process --names nginx postgres redis
```

## Extending

Checkers live in:

- `apps/checkers/checkers/`

To add a new checker:

- Inherit from `BaseChecker`
- Return a `CheckResult`
- Register it in `CHECKER_REGISTRY`

## Troubleshooting

### `ping` permissions / failures

The network checker uses the system `ping` binary. In some environments (containers, locked-down CI), `ping` may be blocked.

Workarounds:

- run without the `network` checker, or
- adjust platform/network permissions

### Disk path not found

If a disk path doesn’t exist, the disk checker returns `UNKNOWN` for that path.
