# Installation

This repo supports **two install paths**:

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
- Prompts you for **dev** or **production** configuration and appends missing `.env` keys
  - It **does not overwrite** existing values
- Installs dependencies with `uv sync`
  - dev installs include **dev extras**
  - prod installs are **runtime-only**
- Runs Django migrations
- Runs `python manage.py check`
- Optionally runs health checks now
- Optionally sets up cron via `./bin/setup_cron.sh`
- Optionally sets up shell aliases via `./bin/setup_aliases.sh`

See the installer implementation in `bin/install.sh`.

---

## 2) Cron setup (optional)

If you didn't enable cron during install, you can run it later:

```bash
./bin/setup_cron.sh
```

### What it does

- Detects the project directory automatically
- Lets you choose a schedule (every 5 min / 15 min / hourly / etc. or custom)
- Writes a `crontab` entry that runs:

```bash
uv run python manage.py check_and_alert --json
```

- Logs output to `cron.log` in the project root

See the cron script in `bin/setup_cron.sh`.

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
./bin/setup_aliases.sh
```

It will prompt for a prefix (default: `sm`), generate aliases, and add a `source` line to your shell profile.

### Key aliases

| Alias | What it does |
|-------|-------------|
| `sm-check-health` | Run health checks (CPU, memory, disk, network, process) |
| `sm-run-pipeline` | Execute pipelines (definition-based or sample) |
| `sm-setup-instance` | Interactive wizard to create pipelines and notification channels |
| `sm-check-and-alert` | Run checks and create alerts/incidents |
| `sm-get-recommendations` | Get AI-powered system recommendations |
| `sm-cli` | Interactive CLI menu |

All aliases pass flags through: `sm-check-health --json` = `uv run python manage.py check_health --json`.

See [`bin/README.md`](../bin/README.md) for the full alias table and script details.

### Custom prefix

```bash
./bin/setup_aliases.sh --prefix maint
# Creates: maint-check-health, maint-run-pipeline, etc.
```

### Remove aliases

```bash
./bin/setup_aliases.sh --remove
```

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

## 5) Manual installation (no scripts)

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

## 6) Common commands

With aliases (after running `./bin/setup_aliases.sh`):

```bash
sm-check-health                  # Run health checks
sm-check-health --list           # List available checkers
sm-check-and-alert --json        # Run checks + create alerts (cron-friendly)
sm-get-recommendations --all     # Get system recommendations
sm-run-pipeline --sample         # Run pipeline with sample alert
```

Without aliases:

```bash
uv run python manage.py check_health
uv run python manage.py check_health --list
uv run python manage.py check_and_alert --json
uv run python manage.py get_recommendations --all
uv run python manage.py run_pipeline --sample
```

---

## 7) Pipeline workflow with aliases

Definition-based pipelines let you compose custom monitoring workflows. Here's the typical workflow using shell aliases:

### Step 1: Create a pipeline and notification channels

```bash
sm-setup-instance
```

The interactive wizard walks you through:
- Choosing which health checkers to enable
- Configuring notification channels (Slack, email, PagerDuty, generic webhook)
- Creating a `PipelineDefinition` in the database (e.g., `local-monitor`)

### Step 2: Validate with dry-run

```bash
sm-run-pipeline --definition local-monitor --dry-run
```

This shows the node chain and config without executing anything.

### Step 3: Run the pipeline

```bash
sm-run-pipeline --definition local-monitor
```

This runs real health checks and sends real notifications through the channels you configured.

### More examples

```bash
# Run from a JSON file instead of a DB definition
sm-run-pipeline --config apps/orchestration/management/commands/pipelines/local-monitor.json

# Run with a sample alert payload (for testing ingest-based pipelines)
sm-run-pipeline --definition local-monitor --sample

# Monitor pipeline run history
sm-monitor-pipeline

# Test notification delivery without running a pipeline
sm-test-notify --driver slack
```

### Without aliases

The same workflow without aliases:

```bash
uv run python manage.py setup_instance
uv run python manage.py run_pipeline --definition local-monitor --dry-run
uv run python manage.py run_pipeline --definition local-monitor
```

For full pipeline documentation including node types, config options, and troubleshooting, see [`apps/orchestration/README.md`](../apps/orchestration/README.md).