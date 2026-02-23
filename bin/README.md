# Shell Scripts & CLI

This directory contains shell scripts for installation, automation, and interactive usage.

[toc]

## Quick Command Reference

All management commands and their shell aliases (set up via `setup_aliases.sh`):

| Alias (default `sm-` prefix) | Management Command | App | Description |
|------|------|-----|-------------|
| `sm-check-health` | `check_health` | checkers | Run health checks (CPU, memory, disk, network, process) |
| `sm-run-check` | `run_check` | checkers | Run a single checker with checker-specific options |
| `sm-check-and-alert` | `check_and_alert` | alerts | Run checks and create alerts/incidents |
| `sm-get-recommendations` | `get_recommendations` | intelligence | Get AI-powered system recommendations |
| `sm-run-pipeline` | `run_pipeline` | orchestration | Execute the full pipeline |
| `sm-monitor-pipeline` | `monitor_pipeline` | orchestration | Monitor pipeline run history |
| `sm-test-notify` | `test_notify` | notify | Test notification delivery |
| `sm-list-notify-drivers` | `list_notify_drivers` | notify | List available notification drivers |
| `sm-setup-instance` | `setup_instance` | orchestration | Interactive wizard to create pipelines and notification channels |
| `sm-cli` | — | — | Interactive CLI menu |

Aliases pass all flags through. Example: `sm-check-health --json` = `uv run python manage.py check_health --json`.

For full flag reference per command, see the app READMEs:
- [`apps/checkers/README.md`](../apps/checkers/README.md) — `check_health` (10 flags), `run_check` (11 flags)
- [`apps/alerts/README.md`](../apps/alerts/README.md) — `check_and_alert` (9 flags)
- [`apps/intelligence/README.md`](../apps/intelligence/README.md) — `get_recommendations` (11 flags)
- [`apps/notify/README.md`](../apps/notify/README.md) — `list_notify_drivers` (1 flag), `test_notify` (14 flags)
- [`apps/orchestration/README.md`](../apps/orchestration/README.md) — `run_pipeline` (12 flags), `monitor_pipeline` (3 flags)

---

## Scripts

### `setup_aliases.sh` — Shell Alias Setup

Set up shell aliases so you can run `sm-check-health` instead of `uv run python manage.py check_health`.

```bash
# Interactive setup (prompts for prefix, default: sm)
./bin/setup_aliases.sh

# Custom prefix
./bin/setup_aliases.sh --prefix maint
# Creates: maint-check-health, maint-run-check, etc.

# Show current aliases
./bin/setup_aliases.sh --list

# Remove aliases and source line from shell profile
./bin/setup_aliases.sh --remove
```

**What it does:**
- Generates `bin/aliases.sh` (gitignored) with aliases locked to the project path
- Adds a `source` line to `~/.zshrc` or `~/.bashrc`
- `--remove` undoes both

**After setup, activate immediately:**
```bash
source ~/.zshrc   # or source ~/.bashrc
```

---

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
- Shell alias setup option

---

### `install.sh` — Project Installer

Full installation script for setting up the project.

```bash
./bin/install.sh
```

**What it does:**
- Verifies Python 3.10+ (tries python3.13 → python3.10 → python3)
- Installs `uv` if missing
- Creates `.env` from `.env.sample`
- Prompts for dev/production configuration
- Installs dependencies with `uv sync`
- Runs Django migrations
- Optionally runs health checks
- Optionally sets up cron
- Optionally sets up shell aliases

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
