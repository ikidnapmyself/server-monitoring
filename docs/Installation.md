---
title: Installation
layout: default
nav_order: 3
---

# Installation

This repo supports **three install modes** — dev, production (bare metal), and Docker:

[toc]

---

## Requirements

- Python **3.10+**
- [`uv`](https://github.com/astral-sh/uv)
- Dependencies are defined in `pyproject.toml`

---

## 1) Quick install

### 1.1 Clone the repo

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
```

### 1.2 Run the installer

```bash
./bin/install.sh
```

If you get "permission denied", run:

```bash
chmod +x ./bin/*.sh
```

### What the installer does (in order)

`./bin/install.sh` performs these steps:

- Verifies Python is **3.10+**
- Installs `uv` if missing
- Ensures you have a `.env` (creates it from `.env.sample` if present)
- Prompts you for **dev**, **production**, or **docker** mode and appends missing `.env` keys
  - It **does not overwrite** existing values
- Installs dependencies with `uv sync`
  - dev installs include **dev extras**
  - prod installs are **runtime-only**
- Runs Django migrations
- Runs `python manage.py check`
- Optionally runs health checks now
- Optionally sets up cron via `./bin/install.sh cron`
- Optionally sets up shell aliases via `./bin/install.sh aliases`

See the installer implementation in `bin/install.sh`.

---

## 2) Cron setup (optional)

If you didn't enable cron during install, you can run it later:

```bash
./bin/install.sh cron
```

### What it does

- Detects the project directory automatically
- Lets you choose a schedule (every 5 min / 15 min / hourly / etc. or custom)
- Writes a `crontab` entry that runs:

```bash
uv run python manage.py run_pipeline --checks-only --json
```

- Logs output to `cron.log` in the project root

See the cron script in `bin/install.sh cron`.

### Useful commands

```bash
crontab -l
tail -f ./cron.log
```

---

## 3) Shell aliases (optional)

Shell aliases let you run `sm-check-health` instead of `uv run python manage.py check_health`.

If you didn't set up aliases during install, run:

```bash
./bin/install.sh aliases
```

It will prompt for a prefix (default: `sm`), generate aliases, and add a `source` line to your shell profile.

### Key aliases

| Alias | What it does |
|-------|-------------|
| `sm-check-health` | Run health checks (CPU, memory, disk, network, process) |
| `sm-run-pipeline` | Execute pipelines (--sample / --checks-only / --file) |
| `sm-check-and-alert` | Run checks through the pipeline (`run_pipeline --checks-only`) |
| `sm-get-recommendations` | Get AI-powered system recommendations |
| `sm-cli` | Interactive CLI menu |

All aliases pass flags through: `sm-check-health --json` = `uv run python manage.py check_health --json`.

See [`bin/README.md`](../bin/README.md) for the full alias table and script details.

### Custom prefix

```bash
./bin/install.sh aliases --prefix maint
# Creates: maint-check-health, maint-run-pipeline, etc.
```

### Remove aliases

```bash
./bin/install.sh aliases --remove
```

---

## Profiles

The installer supports saving and loading configuration profiles for consistent deployments across machines.

### Saving a Profile

After running the installer, save the configuration:

```bash
./bin/install.sh --save-profile prod-web
```

This creates `.install-profile-prod-web` containing all non-sensitive configuration values.

### Loading a Profile

On a new machine, load a saved profile to pre-fill all prompts:

```bash
./bin/install.sh --profile prod-web
```

Values from the profile appear as defaults — press Enter to accept or type a new value to override.

### Non-Interactive Mode

For automated deployments, combine `--profile` with `--yes` to accept all defaults:

```bash
./bin/install.sh --profile prod-web --yes
```

Only secrets (`DJANGO_SECRET_KEY`, `HUB_API_KEY`) will still be prompted since they are never stored in profiles.

---

## 4) Interactive CLI (recommended)

After installation, use the interactive CLI for a guided experience:

```bash
./bin/cli.sh
```

The CLI provides menus for all management commands with their available options.

Direct shortcuts:
```bash
./bin/cli.sh health     # Health monitoring
./bin/cli.sh intel      # Intelligence recommendations
./bin/cli.sh pipeline   # Pipeline orchestration
./bin/cli.sh notify     # Notifications
```

---

## 5) System health check

Verify your installation is working correctly:

```bash
./bin/check_system.sh
```

This auto-detects your deployment mode (dev/prod/docker/systemd) and runs the relevant checks — Python version, uv, `.env`, `.venv`, Django, migrations, pre-commit hooks, Docker containers, or systemd services.

```bash
./bin/check_system.sh --json    # JSON output (for CI/monitoring)
```

---

## 6) Manual installation (no scripts)

Use this if you want full control or you're running in CI.

### 5.1 Clone

```bash
git clone git@github.com:ikidnapmyself/server-monitoring.git
cd server-monitoring
```

### 5.2 Create and activate a virtualenv

```bash
python3 -m venv .venv
. .venv/bin/activate
```

### 5.3 Install uv (via pip)

```bash
python -m pip install --upgrade pip
pip install uv
```

### 5.4 Create your `.env`

```bash
cp .env.sample .env
```

Set at least a secret key (required for real deployments):

```bash
# example
echo 'DJANGO_SECRET_KEY=change-me' >> .env
```

### 5.5 Install dependencies

Production-style (no dev tools):

```bash
uv sync --frozen --no-dev
```

Dev install (includes dev tools/extras):

```bash
uv sync --all-extras --dev
```

### 5.6 Migrate

```bash
uv run --frozen python manage.py migrate --noinput
```

### 5.7 Django system check

```bash
uv run python manage.py check
```

### 5.8 Run the server

```bash
uv run python manage.py runserver
```

---

## 7) Common commands

With aliases (after running `./bin/install.sh aliases`):

```bash
sm-check-health                  # Run health checks
sm-check-health --list           # List available checkers
sm-check-and-alert --json        # Run checks through pipeline (cron-friendly)
sm-get-recommendations --all     # Get system recommendations
sm-run-pipeline --sample         # Run pipeline with sample alert
```

Without aliases:

```bash
uv run python manage.py check_health
uv run python manage.py check_health --list
uv run python manage.py run_pipeline --checks-only --json
uv run python manage.py get_recommendations --all
uv run python manage.py run_pipeline --sample
```

---

## 8) Pipeline workflow with aliases

### Step 1: Configure channels, routing, and (optionally) AI

```bash
sm-cluster        # guided: notification channel + a catch-all routing pipeline
# or manage routing pipelines directly in Django Admin (Orchestration → Pipeline definitions)
uv run python manage.py setup_intelligence   # optional: pick an AI provider
```

The CHECK stage runs all registered checkers by default; a routing `PipelineDefinition`'s
`run_checkers` / `run_intelligence` / `run_notify` flags select which stages run for a
matched incident, and its `channels` are the notify targets.

### Step 2: Preview with a dry-run

```bash
sm-run-pipeline --sample --dry-run
```

### Step 3: Run the pipeline

```bash
sm-run-pipeline --sample          # full demo run (real checks + notify)
sm-run-pipeline --checks-only     # local monitoring: checks → notify only
```

### More examples

```bash
sm-run-pipeline --file alert.json     # run from a JSON payload file
sm-monitor-pipeline                   # pipeline run history
sm-test-notify --driver slack         # test notification delivery
```

### Without aliases

```bash
uv run python manage.py setup_cluster
uv run python manage.py run_pipeline --sample --dry-run
uv run python manage.py run_pipeline --sample
```

Journey/report shortcuts: `manage.py trace <alert|trace_id>` and `manage.py report`.
For full pipeline docs, see [`apps/orchestration/README.md`](../apps/orchestration/README.md).

---

## 9) Production deployment

For production deployment (Nginx + the broker-free inbox drain), see the
[Deployment Guide](Deployment.md). It covers:

- **Docker Compose** — Django (gunicorn) + the `process_inbox` drain (recommended for quick deploys)
- **Bare metal / VPS** — systemd units for gunicorn and the inbox drain
- **Nginx reverse proxy** — static files, proxy headers, SSL termination
- **Webhook ingestion** — durable ingest: record a PENDING run, drain processes it