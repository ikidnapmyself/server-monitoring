# Shell Scripts & CLI

This directory contains shell scripts for installation, automation, and interactive usage.

[toc]

## Quick Command Reference

All management commands and their shell aliases (set up via `install.sh aliases`):

| Alias (default `sm-` prefix) | Management Command | App | Description |
|------|------|-----|-------------|
| `sm-check-health` | `check_health` | checkers | Run health checks (CPU, memory, disk, network, process) |
| `sm-run-check` | `run_check` | checkers | Run a single checker with checker-specific options |
| `sm-check-and-alert` | `run_pipeline --checks-only` | orchestration | Run checks through orchestrated pipeline |
| `sm-get-recommendations` | `get_recommendations` | intelligence | Get AI-powered system recommendations |
| `sm-run-pipeline` | `run_pipeline` | orchestration | Execute the full pipeline |
| `sm-monitor-pipeline` | `monitor_pipeline` | orchestration | Monitor pipeline run history |
| `sm-test-notify` | `test_notify` | notify | Test notification delivery |
| `sm-setup-instance` | `setup_instance` | orchestration | Interactive wizard to create pipelines and notification channels |
| `sm-cli` | — | — | Interactive CLI menu |
| `sm-update` | — | — | Auto-update from origin/main |

Aliases pass all flags through. Example: `sm-check-health --json` = `uv run python manage.py check_health --json`.

See [`docs/Installation.md`](../docs/Installation.md) for setup guide and pipeline workflow examples.

For full flag reference per command, see the app READMEs:
- [`apps/checkers/README.md`](../apps/checkers/README.md) — `check_health` (10 flags), `run_check` (11 flags)
- [`apps/orchestration/README.md`](../apps/orchestration/README.md) — `run_pipeline` (includes checker flags)
- [`apps/intelligence/README.md`](../apps/intelligence/README.md) — `get_recommendations` (11 flags)
- [`apps/notify/README.md`](../apps/notify/README.md) — `test_notify` (14 flags)
- [`apps/orchestration/README.md`](../apps/orchestration/README.md) — `run_pipeline` (12 flags), `monitor_pipeline` (3 flags)

---

## Scripts

### `install.sh aliases` — Shell Alias Setup

Set up shell aliases so you can run `sm-check-health` instead of `uv run python manage.py check_health`.

```bash
# Interactive setup (prompts for prefix, default: sm)
./bin/install.sh aliases

# Custom prefix
./bin/install.sh aliases --prefix maint
# Creates: maint-check-health, maint-run-check, etc.

# Show current aliases
./bin/install.sh aliases --list

# Remove aliases and source line from shell profile
./bin/install.sh aliases --remove
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

**Module structure (`bin/cli/`):**

| Module | Menu functions |
|--------|---------------|
| `install_menu.sh` | Install/setup, installation status |
| `health.sh` | Health checks, environment selection |
| `alerts.sh` | Run checks, orchestrated check pipeline |
| `intelligence.sh` | AI recommendations, custom analysis |
| `pipeline.sh` | Show/run/monitor pipelines |
| `notifications.sh` | Test notifications, driver config |

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

### Profiles

Save and load installer configurations for fleet consistency:

```bash
# Save after install
./bin/install.sh --save-profile prod-web

# Load on another machine (pre-fills all prompts)
./bin/install.sh --profile prod-web

# Fully automated (only prompts for secrets)
./bin/install.sh --profile prod-web --yes
```

Profiles are stored as `.install-profile*` files (gitignored by default). They contain all non-sensitive `.env` values plus installer state (cron schedule, alias prefix, etc.). Secrets (`DJANGO_SECRET_KEY`, `WEBHOOK_SECRET_CLUSTER`) are never saved to profiles.

---

### `install.sh deploy` — Deployment

Deploys the project via Docker Compose or systemd, depending on the install mode. Builds images, starts services, and verifies health.

```bash
# Deploy (auto-detects mode from .env)
./bin/install.sh deploy
```

---

### `install.sh cron` — Cron Setup

Sets up scheduled health checks via cron.

```bash
./bin/install.sh cron
```

**What it does:**
- Detects project directory
- Lets you choose a schedule (5 min / 15 min / hourly / custom)
- Writes crontab entry for `run_pipeline --checks-only --json`
- Logs to `cron.log` in project root
- Optionally sets up automatic updates (`bin/update.sh --rollback --auto-env`)

**Useful commands after setup:**
```bash
crontab -l           # View cron entries
tail -f ./cron.log   # Follow cron output
```

---

### `update.sh` — Auto-Update

Checks for updates from `origin/main` and applies them. Syncs dependencies, runs migrations, and restarts services based on the detected deployment mode.

```bash
# Check and apply updates
./bin/update.sh

# Dry run (show what would happen)
./bin/update.sh --dry-run

# Enable automatic rollback on failure
./bin/update.sh --rollback

# Auto-append new env vars from .env.sample
./bin/update.sh --auto-env

# JSON output (for CI or monitoring)
./bin/update.sh --json
```

**What it does:**
1. `git fetch origin main` — check for new commits
2. `git pull origin main` — apply changes
3. Sync `.env` with `.env.sample` — warn or auto-append new keys
4. `uv sync` — sync dependencies (mode-aware)
5. `python manage.py migrate` — apply database migrations
6. Restart services (systemd, docker compose, or skip for dev)
7. Notify on success or failure (best-effort)

**Flags:**
- `--rollback` — revert to previous version if any step fails
- `--auto-env` — auto-append new `.env.sample` keys to `.env`
- `--dry-run` — preview without applying
- `--json` — JSON output

**Exit codes:** `0` = up to date or updated, `1` = error.

**Cron:** Run `./bin/install.sh cron` and answer "y" to the auto-update prompt.

---

### `check_security.sh` — Security Posture Audit

Audits the security configuration of a deployment. Auto-detects whether this node is an agent, hub, or standalone instance.

```bash
# Run security audit
./bin/check_security.sh

# JSON output (for CI or monitoring)
./bin/check_security.sh --json
```

**Mode detection:**
- **Agent** (`HUB_URL` set): checks TLS, HMAC signing, hub reachability, certificate validity
- **Hub** (`CLUSTER_ENABLED=1`): checks HMAC secret, bind address, reverse proxy, HTTPS termination
- **Standalone**: common checks only (secret key, debug mode, .env permissions, allowed hosts, dependency audit)

**Exit codes:** `0` = all pass, `1` = warnings only, `2` = failures present.

## Permissions

If you get "permission denied", make scripts executable:

```bash
chmod +x ./bin/*.sh
```
