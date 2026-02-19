# Checkers

This Django app provides a small health-check framework and two management commands you can run locally or in automation.

> See [Architecture](../../docs/Architecture.md) for how this app fits in the pipeline (CHECK stage).

## What's included

### Models

- `CheckRun` — Log of **standalone** health check executions
  - Status, message, and metrics from the check
  - Link to created Alert (if check found an issue)
  - Execution timing and correlation ID for tracing

> **Note:** When checks run as part of the pipeline, tracking is handled by `apps.orchestration` via `StageExecution` with `stage="check"`.

### Available checkers

These checkers are registered in `apps/checkers/checkers/__init__.py` (`CHECKER_REGISTRY`):

- `cpu` — CPU usage % (warn ≥ 70, critical ≥ 90)
- `memory` — RAM usage % (warn ≥ 70, critical ≥ 90)
- `disk` — disk usage % by path (warn ≥ 80, critical ≥ 95)
- `disk_macos` — macOS disk analysis: space hogs, old files, cleanup recommendations (warn ≥ 5 GB, critical ≥ 20 GB recoverable). Skips on non-darwin.
- `disk_linux` — Linux disk analysis: apt cache, journal logs, Docker/Snap data, old temp files (warn ≥ 5 GB, critical ≥ 20 GB recoverable). Skips on non-linux.
- `disk_common` — Cross-platform disk analysis: system logs, user caches, temp files, large files in home (warn ≥ 5 GB, critical ≥ 20 GB recoverable).
- `network` — % of hosts reachable via ping (OK ≥ 70%, warning ≥ 50%, else critical)
- `process` — % of named processes running (OK = 100%, warning ≥ 50%, else critical)

List them via:

```bash
uv run python manage.py check_health --list
```

### Skipping/Disabling Checkers

You can disable specific checkers globally via the `CHECKERS_SKIP` setting.

#### Skip ALL checkers (helper)

If you want to disable *every* checker (common when using the app as a pipeline controller and you want
`alerts → intelligence → notify` without diagnostics), set:

```bash
export CHECKERS_SKIP_ALL=1
```

This takes precedence over `CHECKERS_SKIP`.

#### Environment Variable

```bash
# Skip network and process checkers
export CHECKERS_SKIP=network,process

# Then run checks - network and process will be skipped
uv run python manage.py check_and_alert
```

#### Django Settings

In `config/settings.py`:

```python
# Skip specific checkers
CHECKERS_SKIP = ["network", "process"]
```

#### Override at Runtime

Use `--include-skipped` to run all checkers regardless of the skip setting:

```bash
uv run python manage.py check_and_alert --include-skipped
```

Or specify checkers explicitly to bypass the skip list:

```bash
uv run python manage.py check_and_alert --checkers network process
```

#### Programmatic Check

```python
from apps.checkers.checkers import is_checker_enabled, get_enabled_checkers

# Check if a specific checker is enabled
if is_checker_enabled("network"):
    # run network checks

# Get only enabled checkers
enabled = get_enabled_checkers()  # dict excluding skipped checkers
```

### Django Admin

Access the admin interface at `/admin/checkers/` to view check run history:

- **CheckRun** — View all check executions (read-only)
  - Colored status badges (ok/warning/critical/unknown)
  - Duration display
  - Link to created alert (if any)
  - Filter by status, checker name, hostname
  - Search by checker name, hostname, message, trace ID
  - Date hierarchy by execution time

> **Note:** Check runs are audit records and cannot be added/edited manually. They are created automatically when checks are run via management commands or the CheckAlertBridge.

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

## CLI Reference

There are two management commands for running checks. All flags can be passed after aliases too (e.g., `sm-check-health --json`).

### `check_health`

Run all checkers (or a selection) and show a summary.

```bash
# Run ALL registered checkers
uv run python manage.py check_health

# Run specific checkers only
uv run python manage.py check_health cpu memory
uv run python manage.py check_health cpu memory disk network process

# List available checkers and exit
uv run python manage.py check_health --list
```

#### JSON output

```bash
# JSON output (for scripts, cron, piping to jq)
uv run python manage.py check_health --json

# Specific checkers + JSON
uv run python manage.py check_health cpu disk --json
```

#### Exit codes for CI/automation

By default: exit `2` if any CRITICAL, `1` if any UNKNOWN, `0` otherwise.

```bash
# Exit 1 if ANY check is WARNING or CRITICAL (strictest)
uv run python manage.py check_health --fail-on-warning

# Exit 1 only if ANY check is CRITICAL
uv run python manage.py check_health --fail-on-critical

# CI pipeline example: fail build on critical
uv run python manage.py check_health --fail-on-critical --json
```

#### Threshold overrides

Override default warning/critical thresholds for all checkers in this run:

```bash
# Lower thresholds (more sensitive)
uv run python manage.py check_health --warning-threshold 60 --critical-threshold 80

# Higher thresholds (less sensitive)
uv run python manage.py check_health --warning-threshold 85 --critical-threshold 98

# Override thresholds for specific checkers only
uv run python manage.py check_health cpu memory --warning-threshold 75 --critical-threshold 95
```

#### Checker-specific options

These flags are passed to the relevant checker when it runs:

```bash
# Disk: check specific mount points
uv run python manage.py check_health disk --disk-paths / /var /tmp /home

# Network: ping specific hosts
uv run python manage.py check_health network --ping-hosts 8.8.8.8 1.1.1.1 github.com

# Process: verify specific processes are running
uv run python manage.py check_health process --processes nginx postgres redis celery
```

#### Combined examples

```bash
# Full CI check: all checkers, JSON, fail on warning
uv run python manage.py check_health --json --fail-on-warning

# Disk + network with custom targets + thresholds
uv run python manage.py check_health disk network \
  --disk-paths / /var/log \
  --ping-hosts 8.8.8.8 google.com \
  --warning-threshold 75 --critical-threshold 90

# Cron job: all checks, JSON, append to log
uv run python manage.py check_health --json >> /var/log/health-checks.log 2>&1

# Quick smoke test: CPU + memory, fail on critical
uv run python manage.py check_health cpu memory --fail-on-critical
```

#### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `checkers` (positional) | str... | all | Specific checkers to run (space-separated) |
| `--list` | flag | — | List available checkers and exit |
| `--json` | flag | — | Output results as JSON |
| `--fail-on-warning` | flag | — | Exit 1 if any WARNING or CRITICAL |
| `--fail-on-critical` | flag | — | Exit 1 only if any CRITICAL |
| `--warning-threshold` | float | per-checker | Override warning threshold for all checks |
| `--critical-threshold` | float | per-checker | Override critical threshold for all checks |
| `--disk-paths` | str... | `/` | Paths to check (disk checker) |
| `--ping-hosts` | str... | `8.8.8.8 1.1.1.1` | Hosts to ping (network checker) |
| `--processes` | str... | — | Process names to check (process checker) |

---

### `run_check`

Run a **single** checker with checker-specific options.

```bash
# Basic usage
uv run python manage.py run_check cpu
uv run python manage.py run_check memory
uv run python manage.py run_check disk
uv run python manage.py run_check network
uv run python manage.py run_check process
```

#### JSON output

```bash
uv run python manage.py run_check cpu --json
uv run python manage.py run_check disk --json
```

#### Threshold overrides

```bash
# Override thresholds for this single check
uv run python manage.py run_check cpu --warning-threshold 80 --critical-threshold 95
uv run python manage.py run_check memory --warning-threshold 75 --critical-threshold 90
uv run python manage.py run_check disk --warning-threshold 85 --critical-threshold 98
```

#### CPU checker options

```bash
# Default: 5 samples, 1 second apart
uv run python manage.py run_check cpu

# More samples for better accuracy
uv run python manage.py run_check cpu --samples 10

# Faster sampling (0.5s intervals)
uv run python manage.py run_check cpu --sample-interval 0.5

# Quick snapshot (1 sample, no wait)
uv run python manage.py run_check cpu --samples 1 --sample-interval 0

# Per-CPU mode (reports busiest core)
uv run python manage.py run_check cpu --per-cpu

# All CPU options combined
uv run python manage.py run_check cpu --samples 10 --sample-interval 0.5 --per-cpu

# CPU with threshold override + JSON
uv run python manage.py run_check cpu --samples 10 --per-cpu --warning-threshold 80 --critical-threshold 95 --json
```

#### Memory checker options

```bash
# Default: RAM only
uv run python manage.py run_check memory

# Include swap memory in the check
uv run python manage.py run_check memory --include-swap

# Memory with custom thresholds
uv run python manage.py run_check memory --include-swap --warning-threshold 75 --critical-threshold 90 --json
```

#### Disk checker options

```bash
# Default: check /
uv run python manage.py run_check disk

# Check specific paths
uv run python manage.py run_check disk --paths /
uv run python manage.py run_check disk --paths / /var /tmp /home
uv run python manage.py run_check disk --paths /var/log /var/lib

# Disk with thresholds + JSON
uv run python manage.py run_check disk --paths / /var/log --warning-threshold 80 --critical-threshold 95 --json
```

#### Network checker options

```bash
# Default hosts: 8.8.8.8, 1.1.1.1
uv run python manage.py run_check network

# Custom hosts
uv run python manage.py run_check network --hosts 8.8.8.8 1.1.1.1 github.com
uv run python manage.py run_check network --hosts google.com cloudflare.com aws.amazon.com

# Network with JSON
uv run python manage.py run_check network --hosts 8.8.8.8 google.com --json
```

#### Process checker options

```bash
# Check specific processes
uv run python manage.py run_check process --names nginx
uv run python manage.py run_check process --names nginx postgres redis
uv run python manage.py run_check process --names nginx postgres redis celery gunicorn

# Process with JSON
uv run python manage.py run_check process --names nginx postgres --json
```

#### Flag reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `checker` (positional) | str | required | Checker name (cpu, memory, disk, network, process, etc.) |
| `--json` | flag | — | Output as JSON |
| `--warning-threshold` | float | per-checker | Override warning threshold |
| `--critical-threshold` | float | per-checker | Override critical threshold |
| `--samples` | int | 5 | Number of CPU samples (cpu only) |
| `--sample-interval` | float | 1.0 | Seconds between CPU samples (cpu only) |
| `--per-cpu` | flag | — | Per-CPU mode, reports busiest core (cpu only) |
| `--include-swap` | flag | — | Include swap memory (memory only) |
| `--paths` | str... | `/` | Disk paths to check (disk only) |
| `--hosts` | str... | `8.8.8.8 1.1.1.1` | Hosts to ping (network only) |
| `--names` | str... | — | Process names to check (process only) |

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
