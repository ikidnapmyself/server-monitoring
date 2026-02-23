[![CI](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ikidnapmyself/server-monitoring/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ikidnapmyself/server-monitoring/graph/badge.svg)](https://codecov.io/gh/ikidnapmyself/server-monitoring)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/2926298c5268456f866ca414dd7e2cb8)](https://app.codacy.com/gh/ikidnapmyself/server-monitoring/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Django 5.2](https://img.shields.io/badge/django-5.2-green.svg)](https://www.djangoproject.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Linting: Ruff](https://img.shields.io/badge/linting-ruff-orange.svg)](https://github.com/astral-sh/ruff)

[![License: MIT](https://img.shields.io/github/license/ikidnapmyself/server-monitoring)](https://github.com/ikidnapmyself/server-monitoring/blob/main/LICENSE)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg)](https://pre-commit.com/)

# server-maintanence

A small, modular Django project for basic **server maintenance / monitoring** workflows.

This top-level README is the entry point and documentation hub. App-specific docs live alongside the apps.

[toc]

## Documentation map

- Architecture: [`docs/Architecture.md`](docs/Architecture.md)
- Installation: [`docs/Installation.md`](docs/Installation.md)
- Security: [`docs/Security.md`](docs/Security.md)
- Health checks (checkers): [`apps/checkers/README.md`](apps/checkers/README.md)
- Alert ingestion: [`apps/alerts/README.md`](apps/alerts/README.md)
- Notifications: [`apps/notify/README.md`](apps/notify/README.md)
- Intelligence/recommendations: [`apps/intelligence/README.md`](apps/intelligence/README.md)
- Pipeline orchestration: [`apps/orchestration/README.md`](apps/orchestration/README.md)
- Shell scripts & CLI: [`bin/README.md`](bin/README.md)
- Working with repo AI agents / conventions: 
  - [`CLAUDE.md`](CLAUDE.md)
  - [`agents.md`](agents.md)

## Requirements

- Python **3.10+**
- Package manager: **uv** (recommended, repo includes `uv.lock`)

Dependencies (from `pyproject.toml`): Django + psutil.

## Install

See Installation document [`docs/Installation.md`](docs/Installation.md).

## Usage modes

This project supports two modes — see [Architecture](docs/Architecture.md) for full details:

1. **Pipeline controller**: Ingest alerts and route through intelligence + notify stages.
2. **Individual server monitor**: Run health checks locally and optionally generate alerts.

Quick examples:

```bash
# Pipeline mode (sync, with sample alert)
uv run python manage.py run_pipeline --sample

# Standalone health checks
uv run python manage.py check_health

# Run checks and generate alerts
uv run python manage.py check_and_alert
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

4) (Optional) Set up shell aliases for quick command access:

```bash
./bin/setup_aliases.sh
```

After setup, use aliases like `sm-check-health`, `sm-run-check`, etc. See [`bin/README.md`](bin/README.md) for the full alias list.

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
