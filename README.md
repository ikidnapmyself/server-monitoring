# server-maintanence

[![codecov](https://codecov.io/gh/ikidnapmyself/server-monitoring/branch/main/graph/badge.svg)](https://codecov.io/gh/ikidnapmyself/server-monitoring)

A small, modular Django project for basic **server maintenance / monitoring** workflows.

This top-level README is the entry point and documentation hub. App-specific docs live alongside the apps.

[toc]

## Documentation map

- Installation: [`docs/Installation.md`](docs/Installation.md)
- Security: [`docs/Security.md`](docs/Security.md)
- Templates: [`docs/Templates.md`](docs/Templates.md)
- Health checks (checkers): [`apps/checkers/README.md`](apps/checkers/README.md)
- Alert ingestion: [`apps/alerts/README.md`](apps/alerts/README.md)
- Notifications: [`apps/notify/README.md`](apps/notify/README.md)
- Intelligence/recommendations: [`apps/intelligence/README.md`](apps/intelligence/README.md)
- Pipeline orchestration: [`apps/orchestration/README.md`](apps/orchestration/README.md)
- Shell scripts & CLI: [`bin/README.md`](bin/README.md)
- Working with repo AI agents / conventions: [`agents.md`](agents.md)

## Requirements

- Python **3.10+**
- Package manager: **uv** (recommended, repo includes `uv.lock`)

Dependencies (from `pyproject.toml`): Django + psutil.

## Install

See Installation document [`docs/Installation.md`](docs/Installation.md).

## Usage modes

This repo supports two common ways to run it:

- **Pipeline controller**: ingest an alert and route it through `intelligence` and `notify` (optionally skipping `checkers`).
- **Individual server monitor**: run health checks locally on a server and optionally generate alerts.

### 1) Use it like a PIPELINE CONTROLLER (alerts → intelligence → notify), skipping checks

#### What “skipping checks” means in this repo

The orchestrator pipeline order is fixed:

`apps.alerts → apps.checkers → apps.intelligence → apps.notify`

…but you can effectively “skip” the check stage by disabling checkers.

**Recommended (simple):**

```bash
export CHECKERS_SKIP_ALL=1
```

Alternatively, you can disable all checkers by listing them:

```bash
export CHECKERS_SKIP=cpu,memory,disk,network,process
```

So “skip checks” = disable all of them:

```bash
export CHECKERS_SKIP=cpu,memory,disk,network,process
```

With that set, the pipeline still runs, but the check stage has nothing to execute.

#### Run it (pipeline controller)

You need Django running for the orchestration HTTP endpoints, and optionally Celery+Redis for async execution.

Minimum required env:
- `DJANGO_SECRET_KEY` is mandatory (enforced in `config/settings.py`).

##### Option A: run pipeline synchronously (no Celery worker required)

1) Start Django:

```bash
uv run python manage.py migrate
uv run python manage.py runserver
```

2) Call the sync endpoint:
- `POST /orchestration/pipeline/sync/` (see `apps/orchestration/urls.py`)

Example request body (see [`apps/orchestration/README.md`](apps/orchestration/README.md)):

```json
{
  "payload": {
    "alertname": "HighCPU",
    "severity": "critical"
  },
  "source": "grafana",
  "environment": "production"
}
```

##### Option B: run pipeline async (recommended for production-like)

1) Run Redis (default broker is `redis://localhost:6379/0`).

2) Start Django:

```bash
uv run python manage.py runserver
```

3) Start a Celery worker (Celery config is in `config/celery.py`).

4) Trigger:
- `POST /orchestration/pipeline/`

Response is a queued acknowledgement (per orchestration docs):

```json
{
  "status": "queued",
  "task_id": "abc123",
  "message": "Pipeline queued for execution"
}
```

#### How to check results / status

These endpoints exist:
- `GET /orchestration/pipeline/<run_id>/` (status)
- `GET /orchestration/pipelines/?status=failed&limit=10` (list)
- `POST /orchestration/pipeline/<run_id>/resume/` (resume failed)

#### Quick “no-HTTP” way (management command)

The orchestration app includes a management command to test end-to-end:

```bash
# With sample alert
uv run python manage.py run_pipeline --sample

# If you also want to skip checks
CHECKERS_SKIP=cpu,memory,disk,network,process uv run python manage.py run_pipeline --sample
```

This is the easiest way to confirm your `intelligence` + `notify` stages are wired correctly without building webhooks yet.

### 2) Run it as an individual server monitor (standalone checks on one host)

This is exactly what `apps/checkers` calls **Standalone mode**:
- checks run locally
- results are stored in `CheckRun`
- alerts are created via `CheckAlertBridge` if issues are found

#### Typical usage patterns

##### A) Just run checks and see health (no alerting)

`check_health` command:

```bash
# List available checkers
uv run python manage.py check_health --list

# Run all checks
uv run python manage.py check_health

# Run a subset
uv run python manage.py check_health cpu memory disk

# Automation-friendly JSON
uv run python manage.py check_health --json
```

Exit codes for automation are described in [`apps/checkers/README.md`](apps/checkers/README.md).

##### B) Run a single checker

```bash
uv run python manage.py run_check cpu
```

(Also supports checker-specific flags like disk paths, ping hosts, process names; see the checkers README.)

##### C) Run checks and generate alerts (the “monitoring” mode)

Use the command intended for cron/automation:

```bash
uv run python manage.py check_and_alert
```

Then view:
- `/admin/checkers/` (check history)
- `/admin/alerts/` (alerts/incidents)

#### Optional: disable some checks on that server

```bash
# Example: don’t run network/process checks on this host
export CHECKERS_SKIP=network,process
```

You can override at runtime:

```bash
# Run all checkers regardless of skip setting
uv run python manage.py check_and_alert --include-skipped

# Or explicitly run only a list
uv run python manage.py check_and_alert --checkers network process
```

## Environment configuration (.env / dotenv)

This project supports **dotenv** files via `python-dotenv`.

- Create a local `.env` by copying `.env.sample`.
- Optionally use `.env.dev` for dev-only defaults by setting `DJANGO_ENV=dev`.
- Values already present in your shell environment take precedence (dotenv never overrides existing vars).

Common variables:
- `DJANGO_SECRET_KEY` (required in production; local dev can fall back to an insecure default)
- `DJANGO_DEBUG` (`1`/`0`)
- `DJANGO_ALLOWED_HOSTS` (comma-separated)
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_TASK_ALWAYS_EAGER`

## Development

This repo uses `uv` for dependency management and a small, consistent dev-tooling stack configured in `pyproject.toml`:

- **Black** for formatting
- **Ruff** for linting + import sorting
- **pytest + pytest-django** for tests
- **mypy + django-stubs** (optional) for type-checking

### Common commands

```bash
# Install runtime + dev tools
uv sync --extra dev

# Set up pre-commit hooks
uv run pre-commit install

# Run pre-commit manually on all files
uv run pre-commit run --all-files

# Format
uv run black .

# Lint (and auto-fix imports where possible)
uv run ruff check . --fix

# Tests
uv run pytest

# Optional: type-check
uv run mypy .
```


## Quickstart

1) Create a local env file:

```bash
cp .env.sample .env
```

2) Apply migrations:

```bash
uv run python manage.py migrate
```

3) Run the interactive CLI (recommended for new users):

```bash
./bin/cli.sh
```

The CLI guides you through all available commands with their options.

Alternatively, run commands directly:

```bash
# Run the health check suite
uv run python manage.py check_health

# List available checkers
uv run python manage.py check_health --list
```

(Optional) Run the Django server:

```bash
uv run python manage.py runserver
```

## Project layout

- `config/` — Django project settings/urls/asgi/wsgi
- `apps/` — Django apps
  - `apps/checkers/` — health checks + management commands
  - `apps/alerts/` — alert ingestion (scaffold)
  - `apps/notify/` — notification drivers (scaffold)
  - `apps/intelligence/` — intelligence/recommendations system
  - `apps/orchestration/` — pipeline orchestration (alerts → checkers → intelligence → notify)
- `bin/` — shell scripts (installer, cron setup, interactive CLI)

## Contributing / extending

- Start by reading the app-level README for the area you’re changing.
- If you’re using AI agents in this repo, `agents.md` describes the available agent roles and project rules.

## License

MIT License (c) 2026 Burak.

See [`LICENSE`](LICENSE).
