# Shell Scripts

This directory contains shell scripts for installation, automation, and interactive usage.

[toc]

## Scripts

### `cli.sh` — Interactive CLI

An interactive menu-driven interface for all management commands. Recommended for new users and manual operations.

```bash
# Start interactive mode
./bin/cli.sh

# Direct shortcuts
./bin/cli.sh install    # Jump to installation menu
./bin/cli.sh health     # Jump to health monitoring
./bin/cli.sh alerts     # Jump to alerts menu
./bin/cli.sh intel      # Jump to intelligence menu
./bin/cli.sh pipeline   # Jump to pipeline menu
./bin/cli.sh notify     # Jump to notifications menu
./bin/cli.sh help       # Show all options
```

**Features:**
- Color-coded output
- Shows available flags and options for each command
- Confirms before running commands
- Installation status check

**Commands covered:**
- `check_health` — System health metrics
- `run_check` — Run specific checkers
- `check_and_alert` — Run checker with alerting
- `get_recommendations` — AI-powered recommendations
- `run_pipeline` — Execute pipelines
- `monitor_pipeline` — Monitor pipeline execution
- `list_notify_drivers` — List notification drivers
- `test_notify` — Send test notifications

---

### `install.sh` — Project Installer

Full installation script for setting up the project.

```bash
./bin/install.sh
```

**What it does:**
- Verifies Python 3.10+
- Installs `uv` if missing
- Creates `.env` from `.env.sample`
- Prompts for dev/production configuration
- Installs dependencies with `uv sync`
- Runs Django migrations
- Optionally runs health checks
- Optionally sets up cron

See [`docs/Installation.md`](../docs/Installation.md) for full details.

---

### `setup_cron.sh` — Cron Setup

Sets up scheduled health checks via cron.

```bash
./bin/setup_cron.sh
```

**What it does:**
- Detects project directory
- Lets you choose a schedule (5 min / 15 min / hourly / custom)
- Writes crontab entry for `check_and_alert --json`
- Logs to `cron.log` in project root

**Useful commands after setup:**
```bash
crontab -l           # View cron entries
tail -f ./cron.log   # Follow cron output
```

---

## Permissions

If you get "permission denied", make scripts executable:

```bash
chmod +x ./bin/*.sh
```
