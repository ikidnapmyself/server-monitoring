---
title: "bin/ Re-engineering"
parent: Plans
nav_order: 79739672
---

# bin/ Re-engineering — Design

**Date:** 2026-03-27

## Problem

The `bin/` directory contains 6 scripts totaling 2,207 lines with significant code duplication (~150 lines of color defs, logging functions, path resolution, and `command_exists` repeated across 5-6 files). There are zero tests. systemd deployment is fully manual. `cli.sh` is a monolithic 826-line file.

## Goal

Modularize `bin/` into shared libraries, add BATS tests, automate systemd deployment, and break up the CLI — while preserving all existing functionality (dev, prod, docker modes, crons, aliases).

## Target Directory Layout

```
bin/
├── lib/                        # Shared libraries (sourced, never executed directly)
│   ├── colors.sh               # Color constants (RED, GREEN, YELLOW, BLUE, NC, CYAN, BOLD)
│   ├── logging.sh              # info(), success(), warn(), error() — sources colors.sh
│   ├── paths.sh                # SCRIPT_DIR, PROJECT_DIR resolution
│   ├── dotenv.sh               # dotenv_ensure_file, dotenv_has_key, dotenv_set_if_missing, prompt_non_empty
│   ├── docker.sh               # get_service_state(), docker_preflight()
│   └── checks.sh               # command_exists(), check_python(), check_uv()
├── cli/                        # CLI menu modules (sourced by cli.sh)
│   ├── health.sh               # Health checks menu
│   ├── alerts.sh               # Alerts menu
│   ├── intelligence.sh         # Intelligence menu
│   ├── pipeline.sh             # Pipeline menu
│   ├── notifications.sh        # Notifications menu
│   └── install_menu.sh         # Installation/setup menu
├── tests/                      # BATS tests
│   ├── lib/                    # Unit tests for bin/lib/
│   │   ├── test_colors.bats
│   │   ├── test_logging.bats
│   │   ├── test_paths.bats
│   │   ├── test_dotenv.bats
│   │   ├── test_docker.bats
│   │   └── test_checks.bats
│   ├── test_install.bats       # Smoke tests
│   ├── test_deploy_docker.bats
│   ├── test_deploy_systemd.bats
│   ├── test_setup_cron.bats
│   ├── test_setup_aliases.bats
│   ├── test_check_system.bats
│   └── test_cli.bats
├── install.sh                  # Main installer (dev/prod/docker)
├── deploy-docker.sh            # Docker Compose deployment
├── deploy-systemd.sh           # systemd deployment (NEW)
├── setup_cron.sh               # Cron scheduling
├── setup_aliases.sh            # Shell aliases (sm-*)
├── check_system.sh             # System validation
├── cli.sh                      # Interactive CLI (thin dispatcher)
└── README.md                   # Script documentation
```

## Shared Libraries (`bin/lib/`)

Each file is sourceable with no side effects on load (no output, no exits, just definitions).

**`colors.sh`** — Color constants: RED, GREEN, YELLOW, BLUE, CYAN, BOLD, NC.

**`logging.sh`** — Sources `colors.sh`. Provides `info()`, `success()`, `warn()`, `error()` using `printf` (portable, consistent). `error()` writes to stderr.

**`paths.sh`** — Resolves `SCRIPT_DIR` and `PROJECT_DIR`. Takes optional argument for caller's `BASH_SOURCE` so it works when sourced from any script.

**`dotenv.sh`** — Extracted from `install.sh`: `dotenv_ensure_file()`, `dotenv_has_key()`, `dotenv_set_if_missing()`, `prompt_non_empty()`. Sources `paths.sh` for `PROJECT_DIR`.

**`docker.sh`** — Extracted from `deploy-docker.sh`: `get_service_state()` (handles both NDJSON and JSON array from `docker compose ps`), `docker_preflight()` (checks daemon + compose v2). Sources `logging.sh`.

**`checks.sh`** — `command_exists()`, `check_python()` (with pyenv shim detection), `check_uv()`. Extracted from `install.sh`.

**Sourcing pattern:**
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"  # only if needed
```

## `deploy-systemd.sh`

Follows the same pattern as `deploy-docker.sh`: assumes `install.sh` prod mode already handled `.env`/deps.

**Pre-flight checks:**
- Must be run as root (or with sudo)
- `/opt/server-monitoring` exists and has `.venv/`
- `/etc/server-monitoring/env` exists
- Redis service is running (checks both `redis-server` and `redis` unit names)

**Deployment steps:**
1. Copy unit files to `/etc/systemd/system/`
2. `systemctl daemon-reload`
3. Run migrations + collectstatic (as `www-data`)
4. `systemctl enable --now server-monitoring server-monitoring-celery`

**Health verification (60s timeout, same pattern as Docker):**
- `systemctl is-active` for both services
- Stability re-check after one interval (catches crash loops)

**Summary output:** Service status, useful commands (`systemctl status`, `journalctl -u`, `systemctl restart`).

**`install.sh` integration:** Prod mode gets a follow-up prompt: "Deploy with systemd now? [y/N]" which calls `deploy-systemd.sh`. Same delegation pattern as docker mode.

## CLI Modularization

`cli.sh` (826 lines) becomes a thin dispatcher (~100 lines) that:
1. Sources `bin/lib/logging.sh` and `bin/lib/paths.sh`
2. Defines top-level menu and shortcut routing
3. Sources `bin/cli/<module>.sh` on demand

Each module defines one menu function (e.g., `cli_health_menu()`). Direct shortcuts still work: `cli.sh health`, `cli.sh pipeline`, etc.

| Module | What it wraps |
|--------|--------------|
| `health.sh` | `check_health`, `run_check`, `preflight` |
| `alerts.sh` | `test_webhook`, alert listing/filtering |
| `intelligence.sh` | `test_intelligence`, provider management |
| `pipeline.sh` | `run_pipeline`, `monitor_pipeline`, `show_pipeline` |
| `notifications.sh` | `test_notify`, channel management |
| `install_menu.sh` | Installation status, calls to setup scripts |

## Testing Strategy

**BATS setup:** `bats`, `bats-support`, `bats-assert` as git submodules under `bin/tests/test_helper/`. CI integration via GitHub Actions.

**Unit tests (`bin/tests/lib/`):**

| Test file | Coverage |
|-----------|----------|
| `test_colors.bats` | Variables defined and non-empty |
| `test_logging.bats` | Output format, stderr for error() |
| `test_paths.bats` | Resolution from different locations |
| `test_dotenv.bats` | has_key, set_if_missing, ensure_file |
| `test_docker.bats` | JSON array + NDJSON parsing |
| `test_checks.bats` | command_exists exit codes |

**Smoke tests (`bin/tests/`):** No mocking — syntax checks (`bash -n`), graceful failures without prerequisites, flag handling (`--help`, `--list`, `--remove`).

## Phases & PR Strategy

**Phase 1 — Shared libraries + BATS tests** (1 PR)
- Create `bin/lib/` (6 files)
- Set up BATS (git submodules)
- Unit tests for all lib functions
- Add BATS to CI workflow
- No existing scripts change

**Phase 2 — Refactor scripts + deploy-systemd.sh** (1 PR)
- Refactor 5 existing scripts to source `bin/lib/`
- Create `deploy-systemd.sh`
- Update `install.sh` prod mode to offer systemd deployment
- Smoke tests for all scripts
- Update docs (Deployment.md, bin/README.md)

**Phase 3 — CLI modularization** (1 PR)
- Break `cli.sh` into `bin/cli/` modules
- Slim `cli.sh` to thin dispatcher
- Smoke tests
- Update bin/README.md

Each phase is independently mergeable.

## Approach

**Phased (selected):** Libs are tested before scripts depend on them, scripts can be parallelized in Phase 2, each phase is reviewable.

Rejected alternatives:
- **Bottom-up** — too many small PRs, slower
- **Top-down** — one large PR, hard to review, risky