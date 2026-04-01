---
title: "bin/ Re-engineering Phase 2 — Implementation"
parent: Plans
nav_order: 79739672
---

# bin/ Re-engineering Phase 2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor 5 existing bin/ scripts to source `bin/lib/` shared libraries, create `deploy-systemd.sh`, add smoke tests for all scripts, and update docs.

**Architecture:** Each script replaces its inline color/logging/path/dotenv/check code with `source "$SCRIPT_DIR/lib/<lib>.sh"` calls. The `deploy-systemd.sh` follows the same pattern as `deploy-docker.sh`. Smoke tests use BATS to verify syntax, --help flags, and graceful failure without prerequisites.

**Tech Stack:** Bash, BATS, systemd, existing bin/lib/ from Phase 1

---

### Task 1: Refactor `install.sh` to source `bin/lib/`

**Files:**
- Modify: `bin/install.sh`

**What to change:**

Replace lines 7-32 (everything between `set -e` and first function definition) with:

```bash
set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/checks.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"

cd "$PROJECT_DIR"
```

Then **delete** these functions that are now in lib files (they are exact duplicates):
- `command_exists()` (line 30-32) — now in `lib/checks.sh`
- `dotenv_ensure_file()` (lines 34-51) — now in `lib/dotenv.sh`
- `dotenv_has_key()` (lines 53-57) — now in `lib/dotenv.sh`
- `dotenv_set_if_missing()` (lines 59-69) — now in `lib/dotenv.sh`
- `prompt_non_empty()` (lines 71-82) — now in `lib/dotenv.sh`
- `check_python()` (lines 276-302) — now in `lib/checks.sh`
- `check_uv()` (lines 304-332) — now in `lib/checks.sh`

**Keep** these functions that are unique to install.sh:
- `dotenv_prompt_setup()` (lines 84-215) — install-specific interactive prompts
- `dotenv_prompt_docker()` (lines 217-270) — docker-specific interactive prompts

**Important behavior change:** The lib versions of `check_python` and `check_uv` use `return 1` instead of `exit 1`. In the main body where they're called (lines 387-388), wrap them:

```bash
check_python || exit 1
check_uv || exit 1
```

**Step 1: Make the changes**

Read `bin/install.sh` fully. Replace the header (lines 7-32) with source lines. Delete the 7 duplicated functions. Update `check_python`/`check_uv` calls to `|| exit 1`. Keep all unique functions and the main body intact.

**Step 2: Verify syntax**

Run: `bash -n bin/install.sh`
Expected: No errors

**Step 3: Verify lib functions are accessible**

Run: `bash -c 'source bin/lib/dotenv.sh && type dotenv_has_key'`
Expected: Shows function definition

**Step 4: Commit**

```bash
git add bin/install.sh
git commit -m "refactor: install.sh sources bin/lib/ instead of inline helpers"
```

---

### Task 2: Refactor `deploy-docker.sh` to source `bin/lib/`

**Files:**
- Modify: `bin/deploy-docker.sh`

**What to change:**

Replace lines 7-58 (set -e through get_service_state function) with:

```bash
set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/docker.sh"
source "$SCRIPT_DIR/lib/paths.sh"

COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"
cd "$PROJECT_DIR"
```

Delete:
- Color variables (lines 21-25)
- Logging functions (lines 28-31)
- `get_service_state()` function (lines 36-58) — now in `lib/docker.sh`

**Important:** The lib's `get_service_state` takes TWO arguments `(compose_file, service)` while the current inline version takes ONE `(service)` and uses the global `$COMPOSE_FILE`. Update all call sites in the health verification section:

- `get_service_state web` → `get_service_state "$COMPOSE_FILE" web`
- `get_service_state celery` → `get_service_state "$COMPOSE_FILE" celery`

There are 4 call sites total (lines 142, 151, 164, 165).

Also replace the inline pre-flight checks (lines 70-99) with `docker_preflight` from the lib, but keep the `.env` check since `docker_preflight` doesn't do that:

```bash
# Check .env
info "Checking for .env file..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    error ".env file not found."
    echo "  Run ./bin/install.sh first, or copy .env.sample to .env and configure it."
    exit 1
fi
success ".env file found"

# Docker pre-flight (daemon + compose v2)
docker_preflight || exit 1
```

**Step 1: Make the changes**
**Step 2: Verify syntax:** `bash -n bin/deploy-docker.sh`
**Step 3: Commit**

```bash
git add bin/deploy-docker.sh
git commit -m "refactor: deploy-docker.sh sources bin/lib/ instead of inline helpers"
```

---

### Task 3: Refactor `check_system.sh` to source `bin/lib/`

**Files:**
- Modify: `bin/check_system.sh`

**What to change:**

Replace lines 7-19 (colors and path resolution) with:

```bash
set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/colors.sh"
source "$SCRIPT_DIR/lib/paths.sh"

cd "$PROJECT_DIR"
```

Note: `check_system.sh` uses its own `check_pass/check_warn/check_fail` functions (not the standard `info/success/warn/error`), so we only source `colors.sh` and `paths.sh`. The `BOLD` variable comes from `colors.sh`. The `command -v` on line 56 stays inline — it's a one-off, not worth importing all of `checks.sh`.

Delete: lines 10-14 (RED, GREEN, YELLOW, BOLD, NC) and lines 17-19 (SCRIPT_DIR, PROJECT_DIR).

**Step 1: Make the changes**
**Step 2: Verify syntax:** `bash -n bin/check_system.sh`
**Step 3: Commit**

```bash
git add bin/check_system.sh
git commit -m "refactor: check_system.sh sources bin/lib/ instead of inline helpers"
```

---

### Task 4: Refactor `setup_cron.sh` to source `bin/lib/`

**Files:**
- Modify: `bin/setup_cron.sh`

**What to change:**

Replace lines 7-35 (colors, logging functions, path resolution) with:

```bash
set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/paths.sh"

cd "$PROJECT_DIR"
```

Delete:
- Lines 10-14: color variables
- Lines 16-30: info/success/warn/error functions
- Lines 33-35: SCRIPT_DIR/PROJECT_DIR

Keep the rest of the script unchanged (the menu, cron logic, summary).

**Step 1: Make the changes**
**Step 2: Verify syntax:** `bash -n bin/setup_cron.sh`
**Step 3: Commit**

```bash
git add bin/setup_cron.sh
git commit -m "refactor: setup_cron.sh sources bin/lib/ instead of inline helpers"
```

---

### Task 5: Refactor `setup_aliases.sh` to source `bin/lib/`

**Files:**
- Modify: `bin/setup_aliases.sh`

**What to change:**

Replace lines 13-32 (colors, paths, helpers) with:

```bash
set -euo pipefail

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/paths.sh"

ALIASES_FILE="$SCRIPT_DIR/aliases.sh"
MARKER="# server-maintanence aliases"
```

Delete:
- Lines 16-20: color variables
- Lines 23-24: SCRIPT_DIR/PROJECT_DIR
- Lines 29-32: info/success/warn/error functions

Keep `detect_profile`, `show_help`, `show_list`, `generate_aliases`, `install_source_line`, `do_remove`, `main` — all unique to this script.

**Note on label differences:** The current setup_aliases.sh uses `[info]`, `[ok]`, `[warn]`, `[error]` labels while the lib uses `[INFO]`, `[OK]`, `[WARN]`, `[ERROR]`. The lib's labels will now be used. This is an intentional unification — the design doc specifies consistent output.

**Step 1: Make the changes**
**Step 2: Verify syntax:** `bash -n bin/setup_aliases.sh`
**Step 3: Verify flags work:** `bin/setup_aliases.sh --help` and `bin/setup_aliases.sh --list`
**Step 4: Commit**

```bash
git add bin/setup_aliases.sh
git commit -m "refactor: setup_aliases.sh sources bin/lib/ instead of inline helpers"
```

---

### Task 6: Create `deploy-systemd.sh`

**Files:**
- Create: `bin/deploy-systemd.sh`

**Step 1: Create the script**

```bash
#!/bin/bash
#
# systemd deployment script for server-maintanence
# Installs systemd units, runs migrations, and starts services.
# Assumes install.sh prod mode already handled .env and dependencies.
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/checks.sh"
source "$SCRIPT_DIR/lib/paths.sh"

UNIT_DIR="$PROJECT_DIR/deploy/systemd"
INSTALL_DIR="/opt/server-monitoring"
ENV_FILE="/etc/server-monitoring/env"

# ===========================================
#   Pre-flight checks
# ===========================================

echo ""
echo "============================================"
echo "   systemd Deployment — Pre-flight Checks"
echo "============================================"
echo ""

# Must be root
info "Checking privileges..."
if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi
success "Running as root"

# Project dir exists with .venv
info "Checking installation at $INSTALL_DIR..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    error "$INSTALL_DIR/.venv not found."
    echo "  Run install.sh in prod mode first to set up the project."
    exit 1
fi
success "Installation found at $INSTALL_DIR"

# Environment file exists
info "Checking environment file..."
if [ ! -f "$ENV_FILE" ]; then
    error "$ENV_FILE not found."
    echo "  Create it with your production environment variables."
    echo "  See: docs/Deployment.md"
    exit 1
fi
success "Environment file found"

# Redis is running (check both unit names)
info "Checking Redis service..."
if systemctl is-active --quiet redis-server 2>/dev/null; then
    success "Redis is running (redis-server)"
elif systemctl is-active --quiet redis 2>/dev/null; then
    success "Redis is running (redis)"
else
    error "Redis service is not running."
    echo "  Install and start Redis:"
    echo "    Debian/Ubuntu: sudo apt install redis-server && sudo systemctl enable --now redis-server"
    echo "    RHEL/Fedora:   sudo dnf install redis && sudo systemctl enable --now redis"
    exit 1
fi

echo ""
success "All pre-flight checks passed"

# ===========================================
#   Deploy
# ===========================================

echo ""
echo "============================================"
echo "   Deploying systemd services"
echo "============================================"
echo ""

# Copy unit files
info "Installing systemd unit files..."
cp "$UNIT_DIR/server-monitoring.service" /etc/systemd/system/
cp "$UNIT_DIR/server-monitoring-celery.service" /etc/systemd/system/
success "Unit files installed"

# Reload systemd
info "Reloading systemd daemon..."
systemctl daemon-reload
success "systemd reloaded"

# Run migrations and collectstatic as www-data
info "Running migrations..."
sudo -u www-data bash -c "cd $INSTALL_DIR && set -a && source $ENV_FILE && set +a && .venv/bin/python manage.py migrate --noinput"
success "Migrations applied"

info "Collecting static files..."
sudo -u www-data bash -c "cd $INSTALL_DIR && set -a && source $ENV_FILE && set +a && .venv/bin/python manage.py collectstatic --noinput"
success "Static files collected"

# Enable and start services
info "Enabling and starting services..."
systemctl enable --now server-monitoring server-monitoring-celery
success "Services enabled and started"

# ===========================================
#   Health Verification
# ===========================================

echo ""
echo "============================================"
echo "   Health Verification"
echo "============================================"
echo ""

info "Verifying service health (timeout: 60s)..."

TIMEOUT=60
INTERVAL=5
ELAPSED=0
WEB_OK=false
CELERY_OK=false

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if [ "$WEB_OK" = false ]; then
        if systemctl is-active --quiet server-monitoring 2>/dev/null; then
            WEB_OK=true
            success "server-monitoring — active"
        fi
    fi

    if [ "$CELERY_OK" = false ]; then
        if systemctl is-active --quiet server-monitoring-celery 2>/dev/null; then
            CELERY_OK=true
            success "server-monitoring-celery — active"
        fi
    fi

    if [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
        info "Both services active — confirming stability..."
        sleep "$INTERVAL"

        if ! systemctl is-active --quiet server-monitoring 2>/dev/null; then
            WEB_OK=false
            warn "server-monitoring was active but is now failing (crash loop detected)"
        fi
        if ! systemctl is-active --quiet server-monitoring-celery 2>/dev/null; then
            CELERY_OK=false
            warn "server-monitoring-celery was active but is now failing (crash loop detected)"
        fi

        if [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
            break
        fi
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

# Report failures
FAILED=false
if [ "$WEB_OK" = false ]; then
    error "server-monitoring failed to start. Check: journalctl -u server-monitoring"
    FAILED=true
fi
if [ "$CELERY_OK" = false ]; then
    error "server-monitoring-celery failed to start. Check: journalctl -u server-monitoring-celery"
    FAILED=true
fi

if [ "$FAILED" = true ]; then
    exit 1
fi

echo ""
success "All services are healthy"

# ===========================================
#   Summary
# ===========================================

echo ""
echo "============================================"
printf "   %b systemd Deployment Complete!%b\n" "$GREEN" "$NC"
echo "============================================"
echo ""

echo "Services:"
echo "  - server-monitoring          (gunicorn on unix socket)"
echo "  - server-monitoring-celery   (celery worker)"
echo ""
echo "Useful commands:"
echo "  systemctl status server-monitoring"
echo "  systemctl status server-monitoring-celery"
echo "  journalctl -u server-monitoring -f"
echo "  journalctl -u server-monitoring-celery -f"
echo "  systemctl restart server-monitoring server-monitoring-celery"
echo ""
```

**Step 2: Make executable**

```bash
chmod +x bin/deploy-systemd.sh
```

**Step 3: Verify syntax:** `bash -n bin/deploy-systemd.sh`

**Step 4: Commit**

```bash
git add bin/deploy-systemd.sh
git commit -m "feat: add deploy-systemd.sh for automated systemd deployment"
```

---

### Task 7: Add systemd prompt to `install.sh` prod mode

**Files:**
- Modify: `bin/install.sh`

**What to change:**

After the aliases prompt block (currently near the end of the prod path), before `success "Setup complete!"`, add:

```bash
    # Offer systemd deployment (prod only)
    if [ "$INSTALL_MODE" = "prod" ]; then
        echo ""
        read -p "Would you like to deploy with systemd now? [y/N] " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            info "Handing off to deploy-systemd.sh..."
            echo "  Note: This requires root privileges."
            exec sudo "$SCRIPT_DIR/deploy-systemd.sh"
        fi
    fi
```

**Step 1: Make the change**
**Step 2: Verify syntax:** `bash -n bin/install.sh`
**Step 3: Commit**

```bash
git add bin/install.sh
git commit -m "feat: add systemd deployment prompt to install.sh prod mode"
```

---

### Task 8: Add smoke tests for all scripts

**Files:**
- Create: `bin/tests/test_install.bats`
- Create: `bin/tests/test_deploy_docker.bats`
- Create: `bin/tests/test_deploy_systemd.bats`
- Create: `bin/tests/test_setup_cron.bats`
- Create: `bin/tests/test_setup_aliases.bats`
- Create: `bin/tests/test_check_system.bats`

**Step 1: Create all smoke tests**

`bin/tests/test_install.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "install.sh passes syntax check" {
    run bash -n "$BIN_DIR/install.sh"
    assert_success
}

@test "install.sh exists and is executable" {
    [ -x "$BIN_DIR/install.sh" ]
}
```

`bin/tests/test_deploy_docker.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "deploy-docker.sh passes syntax check" {
    run bash -n "$BIN_DIR/deploy-docker.sh"
    assert_success
}

@test "deploy-docker.sh exits 1 without .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    # Override PROJECT_DIR so .env won't be found
    run bash -c 'export PROJECT_DIR="'"$tmpdir"'" && source "'"$BIN_DIR/lib/paths.sh"'" && bash "'"$BIN_DIR/deploy-docker.sh"'"'
    rm -rf "$tmpdir"
    assert_failure
}
```

`bin/tests/test_deploy_systemd.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "deploy-systemd.sh passes syntax check" {
    run bash -n "$BIN_DIR/deploy-systemd.sh"
    assert_success
}

@test "deploy-systemd.sh exits 1 when not root" {
    if [ "$(id -u)" -eq 0 ]; then
        skip "Running as root, cannot test non-root failure"
    fi
    run bash "$BIN_DIR/deploy-systemd.sh"
    assert_failure
    assert_output --partial "root"
}
```

`bin/tests/test_setup_cron.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "setup_cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_cron.sh"
    assert_success
}
```

`bin/tests/test_setup_aliases.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "setup_aliases.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_aliases.sh"
    assert_success
}

@test "setup_aliases.sh --help shows usage" {
    run "$BIN_DIR/setup_aliases.sh" --help
    assert_success
    assert_output --partial "Usage"
}

@test "setup_aliases.sh --list without aliases file warns" {
    # Ensure no aliases file exists
    rm -f "$BIN_DIR/aliases.sh"
    run "$BIN_DIR/setup_aliases.sh" --list
    assert_failure
    assert_output --partial "No aliases file"
}
```

`bin/tests/test_check_system.bats`:
```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "check_system.sh passes syntax check" {
    run bash -n "$BIN_DIR/check_system.sh"
    assert_success
}

@test "check_system.sh --help shows usage" {
    run "$BIN_DIR/check_system.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "--shell-only"
}
```

**Step 2: Update CI to also run smoke tests**

Modify `.github/workflows/ci.yml` — change the BATS run command from:
```yaml
run: ./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/
```
to:
```yaml
run: ./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/
```

This runs both unit tests (`bin/tests/lib/`) and smoke tests (`bin/tests/`).

**Step 3: Run all tests**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
Expected: All tests pass (36 unit + ~12 smoke)

**Step 4: Commit**

```bash
git add bin/tests/ .github/workflows/ci.yml
git commit -m "test: add smoke tests for all bin/ scripts"
```

---

### Task 9: Update docs

**Files:**
- Modify: `docs/Deployment.md`
- Modify: `bin/README.md`

**Step 1: Update Deployment.md**

In the systemd section (after "### 2.5 Install systemd units"), add the same kind of quick-start note used for Docker:

> **Quick start:** Run `./bin/install.sh` in **prod** mode — it offers to deploy systemd units automatically at the end.

Also add after the manual `systemctl enable --now` step:

> Or run `sudo ./bin/deploy-systemd.sh` directly if `.env` and dependencies are already set up.

**Step 2: Update bin/README.md**

Add `deploy-systemd.sh` to the scripts reference, described as: "systemd deployment — installs unit files, runs migrations, starts and verifies services. Called by `install.sh` (prod mode) or run standalone with sudo."

**Step 3: Commit**

```bash
git add docs/Deployment.md bin/README.md
git commit -m "docs: add deploy-systemd.sh to Deployment.md and bin/README.md"
```