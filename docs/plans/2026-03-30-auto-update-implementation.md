---
title: "2026-03-30 Auto-Update Script Implementation Plan"
parent: Plans
---

# Auto-Update Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement `bin/update.sh` — a shell script that auto-updates from `origin/main` with mode-aware restarts, rollback support, env file sync, and notification on success/failure.

**Architecture:** Pure shell script sourcing `bin/lib/` helpers. A new `bin/lib/update.sh` library holds the update logic, following the pattern of `bin/lib/health_check.sh` and `bin/lib/security_check.sh`. The main script `bin/update.sh` parses flags and calls the library. `bin/setup_cron.sh` gets a new prompt for auto-updates.

**Tech Stack:** Bash, git, existing `bin/lib/` helpers (colors, paths, logging, checks, health_check for `detect_mode`).

---

### Task 1: Create the update library skeleton

**Files:**
- Create: `bin/lib/update.sh`

**Step 1: Create `bin/lib/update.sh` with state, logging, and mode detection**

```bash
#!/usr/bin/env bash
#
# Auto-update library.
# Pulls from origin/main, syncs deps, migrates, restarts services.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_UPDATE_LOADED:-}" ]] && return 0
_LIB_UPDATE_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/colors.sh"
source "$_LIB_DIR/paths.sh"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/checks.sh"
source "$_LIB_DIR/health_check.sh"

# --- State ---

_up_dry_run=false
_up_rollback_enabled=false
_up_json_mode=false
_up_log_file="$PROJECT_DIR/update.log"
_up_auto_env=false
_up_saved_sha=""
_up_new_sha=""
_up_failed_step=""
_up_mode=""

# --- Logging ---

_up_log() {
    local level="$1"
    shift
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local msg="[$timestamp] [$level] $*"
    echo "$msg" >> "$_up_log_file"
    if [ "$_up_json_mode" = false ]; then
        case "$level" in
            INFO)  info "$@" ;;
            OK)    success "$@" ;;
            WARN)  warn "$@" ;;
            ERROR) error "$@" ;;
        esac
    fi
}

# --- Notification (best-effort) ---

_up_notify() {
    local title="$1" message="$2" severity="${3:-info}"

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would notify — $title: $message"
        return 0
    fi

    if [ ! -d "$PROJECT_DIR/.venv" ] || ! command_exists uv; then
        _up_log "WARN" "Cannot notify (no .venv or uv) — $title: $message"
        return 0
    fi

    # Best-effort: suppress all errors
    uv run python manage.py test_notify \
        --non-interactive \
        --title "$title" \
        --message "$message" \
        --severity "$severity" \
        &>/dev/null || true
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output (clean parse)

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add update library skeleton with logging and notification"
```

---

### Task 2: Implement the update check (git fetch + compare)

**Files:**
- Modify: `bin/lib/update.sh`

**Step 1: Add the update check function after the notification section**

```bash
# --- Update check ---

_up_check_for_updates() {
    _up_log "INFO" "Fetching from origin/main..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would run git fetch origin main"
        # Still fetch in dry-run so we can report what would change
    fi

    if ! git -C "$PROJECT_DIR" fetch origin main --quiet 2>/dev/null; then
        _up_log "ERROR" "git fetch failed — check network connectivity and git remote config"
        return 1
    fi

    _up_saved_sha=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    local remote_sha
    remote_sha=$(git -C "$PROJECT_DIR" rev-parse origin/main)

    if [ "$_up_saved_sha" = "$remote_sha" ]; then
        _up_log "INFO" "Already up to date ($(_up_short_sha "$_up_saved_sha"))"
        return 2  # Special: up to date, not an error
    fi

    local commit_count
    commit_count=$(git -C "$PROJECT_DIR" rev-list HEAD..origin/main --count)
    _up_log "INFO" "Update available: $commit_count new commit(s) ($(_up_short_sha "$_up_saved_sha") → $(_up_short_sha "$remote_sha"))"
    return 0
}

_up_short_sha() {
    echo "${1:0:7}"
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add git fetch and update check logic"
```

---

### Task 3: Implement the update steps (pull, sync, migrate, restart)

**Files:**
- Modify: `bin/lib/update.sh`

**Step 1: Add the update step functions**

```bash
# --- Update steps ---

_up_pull() {
    _up_log "INFO" "Pulling from origin/main..."
    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would run git pull origin main"
        return 0
    fi

    if ! git -C "$PROJECT_DIR" pull origin main --quiet 2>&1; then
        _up_failed_step="git pull"
        _up_log "ERROR" "git pull failed"
        return 1
    fi

    _up_new_sha=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    _up_log "OK" "Pulled to $(_up_short_sha "$_up_new_sha")"
}

_up_sync_deps() {
    _up_mode=$(detect_mode)
    _up_log "INFO" "Syncing dependencies (mode: $_up_mode)..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would sync dependencies for $_up_mode mode"
        return 0
    fi

    # Docker mode: deps are handled by docker compose build in restart step
    if [ "$_up_mode" = "docker" ]; then
        _up_log "INFO" "Docker mode — deps will sync during image rebuild"
        return 0
    fi

    if ! command_exists uv; then
        _up_failed_step="uv sync"
        _up_log "ERROR" "uv not found — cannot sync dependencies"
        return 1
    fi

    local sync_cmd="uv sync"
    if [ "$_up_mode" = "dev" ]; then
        sync_cmd="uv sync --all-extras --dev"
    fi

    if ! (cd "$PROJECT_DIR" && $sync_cmd 2>&1); then
        _up_failed_step="uv sync"
        _up_log "ERROR" "Dependency sync failed"
        return 1
    fi

    _up_log "OK" "Dependencies synced"
}

_up_migrate() {
    _up_log "INFO" "Running database migrations..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would run migrations"
        return 0
    fi

    # Docker mode: migrations run inside the container
    if [ "$_up_mode" = "docker" ]; then
        _up_log "INFO" "Docker mode — migrations will run inside container"
        return 0
    fi

    if ! (cd "$PROJECT_DIR" && uv run python manage.py migrate --no-input 2>&1); then
        _up_failed_step="migrate"
        _up_log "ERROR" "Database migration failed"
        return 1
    fi

    _up_log "OK" "Migrations applied"
}

_up_restart() {
    _up_log "INFO" "Restarting services (mode: $_up_mode)..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry run: would restart services for $_up_mode mode"
        return 0
    fi

    case "$_up_mode" in
        dev)
            _up_log "INFO" "Dev mode — no restart needed (runserver auto-reloads)"
            ;;
        prod|systemd)
            if ! sudo systemctl restart server-monitoring server-monitoring-celery 2>&1; then
                _up_failed_step="restart"
                _up_log "ERROR" "systemd restart failed"
                return 1
            fi
            _up_log "OK" "systemd services restarted"
            ;;
        docker)
            local compose_file="$PROJECT_DIR/deploy/docker/docker-compose.yml"
            if ! docker compose -f "$compose_file" up -d --build 2>&1; then
                _up_failed_step="restart"
                _up_log "ERROR" "docker compose restart failed"
                return 1
            fi
            _up_log "OK" "Docker containers rebuilt and restarted"
            ;;
        *)
            _up_log "WARN" "Unknown mode '$_up_mode' — skipping restart"
            ;;
    esac
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add update steps (pull, sync, migrate, restart)"
```

---

### Task 4: Implement env file sync

**Files:**
- Modify: `bin/lib/update.sh`

**Step 1: Add the env sync function after `_up_pull` and before `_up_sync_deps`**

```bash
# --- Env file sync ---

_up_sync_env() {
    local env_file="$PROJECT_DIR/.env"
    local sample_file="$PROJECT_DIR/.env.sample"

    if [ ! -f "$sample_file" ]; then
        _up_log "INFO" "No .env.sample found — skipping env sync"
        return 0
    fi

    if [ ! -f "$env_file" ]; then
        _up_log "WARN" ".env not found — skipping env sync"
        return 0
    fi

    # Find keys in .env.sample that are missing from .env
    local missing_keys=()
    local missing_lines=()
    while IFS= read -r line; do
        # Skip comments and blank lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$line" ]] && continue

        local key
        key=$(echo "$line" | cut -d'=' -f1 | tr -d '[:space:]')
        [ -z "$key" ] && continue

        if ! grep -q "^${key}=" "$env_file" 2>/dev/null; then
            missing_keys+=("$key")
            missing_lines+=("$line")
        fi
    done < "$sample_file"

    if [ "${#missing_keys[@]}" -eq 0 ]; then
        _up_log "INFO" ".env is up to date with .env.sample"
        return 0
    fi

    if [ "$_up_auto_env" = true ]; then
        _up_log "INFO" "Auto-appending ${#missing_keys[@]} missing key(s) to .env"
        echo "" >> "$env_file"
        echo "# --- Added by update.sh ($(date '+%Y-%m-%d %H:%M:%S')) ---" >> "$env_file"
        for line in "${missing_lines[@]}"; do
            echo "$line" >> "$env_file"
            _up_log "INFO" "  Added: $line"
        done
    else
        _up_log "WARN" "${#missing_keys[@]} new key(s) in .env.sample not in .env:"
        for i in "${!missing_keys[@]}"; do
            _up_log "WARN" "  ${missing_keys[$i]} (sample: ${missing_lines[$i]})"
        done
        _up_log "WARN" "Run with --auto-env to add them automatically, or add manually to .env"
    fi
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add env file sync (--auto-env flag)"
```

---

### Task 5: Implement rollback

**Files:**
- Modify: `bin/lib/update.sh`

**Step 1: Add the rollback function**

```bash
# --- Rollback ---

_up_rollback() {
    if [ -z "$_up_saved_sha" ]; then
        _up_log "ERROR" "No saved SHA to rollback to"
        return 1
    fi

    _up_log "WARN" "Rolling back to $(_up_short_sha "$_up_saved_sha")..."

    if ! git -C "$PROJECT_DIR" reset --hard "$_up_saved_sha" 2>&1; then
        _up_log "ERROR" "git reset failed — manual intervention required"
        return 1
    fi

    _up_log "INFO" "Code rolled back. Re-syncing dependencies..."

    # Re-sync deps and migrate after rollback
    _up_sync_deps || true
    _up_migrate || true
    _up_restart || true

    _up_log "OK" "Rollback complete"
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add rollback support for failed updates"
```

---

### Task 6: Implement the orchestrator

**Files:**
- Modify: `bin/lib/update.sh`

**Step 1: Add the main `run_update` orchestrator function**

```bash
# --- Orchestrator ---

run_update() {
    _up_log "INFO" "=== Update check started ==="

    # Check for updates
    _up_check_for_updates
    local check_result=$?

    if [ "$check_result" -eq 2 ]; then
        # Up to date
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"up_to_date","sha":"%s"}\n' "$_up_saved_sha"
        fi
        return 0
    elif [ "$check_result" -ne 0 ]; then
        # Fetch failed
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"error","step":"fetch","message":"git fetch failed"}\n'
        fi
        return 1
    fi

    # Run update steps
    local steps=("_up_pull" "_up_sync_env" "_up_sync_deps" "_up_migrate" "_up_restart")
    local failed=false

    for step in "${steps[@]}"; do
        if ! $step; then
            failed=true
            break
        fi
    done

    if [ "$failed" = true ]; then
        local commit_count
        commit_count=$(git -C "$PROJECT_DIR" rev-list "$_up_saved_sha"..HEAD --count 2>/dev/null || echo "?")
        local err_msg="Failed at step: $_up_failed_step. Rolled back: "

        if [ "$_up_rollback_enabled" = true ]; then
            _up_rollback
            err_msg="${err_msg}yes"
        else
            err_msg="${err_msg}no"
        fi

        _up_log "ERROR" "$err_msg"
        _up_notify "Update Failed" "$err_msg" "critical"

        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"error","step":"%s","old_sha":"%s","rolled_back":%s}\n' \
                "$_up_failed_step" "$_up_saved_sha" \
                "$([ "$_up_rollback_enabled" = true ] && echo "true" || echo "false")"
        fi

        _up_log "INFO" "=== Update failed ==="
        return 1
    fi

    # Success
    local commit_count
    commit_count=$(git -C "$PROJECT_DIR" rev-list "$_up_saved_sha".."$_up_new_sha" --count 2>/dev/null || echo "?")
    _up_log "OK" "Update complete: $(_up_short_sha "$_up_saved_sha") → $(_up_short_sha "$_up_new_sha") ($commit_count commits)"

    _up_notify \
        "Update Succeeded" \
        "Updated from $(_up_short_sha "$_up_saved_sha") to $(_up_short_sha "$_up_new_sha") ($commit_count commits)" \
        "success"

    if [ "$_up_json_mode" = true ]; then
        printf '{"status":"updated","old_sha":"%s","new_sha":"%s","commits":%s,"mode":"%s"}\n' \
            "$_up_saved_sha" "$_up_new_sha" "$commit_count" "$_up_mode"
    fi

    _up_log "INFO" "=== Update succeeded ==="
    return 0
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/update.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/update.sh
git commit -m "feat: add update orchestrator with JSON output and notifications"
```

---

### Task 7: Create the main script

**Files:**
- Create: `bin/update.sh`

**Step 1: Create `bin/update.sh`**

```bash
#!/bin/bash
#
# Auto-update script for server-maintanence
# Pulls from origin/main, syncs deps, migrates, restarts.
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/update.sh"

cd "$PROJECT_DIR"

# Parse flags
for arg in "$@"; do
    case $arg in
        --rollback) _up_rollback_enabled=true ;;
        --auto-env) _up_auto_env=true ;;
        --dry-run) _up_dry_run=true ;;
        --json) _up_json_mode=true ;;
        --help|-h)
            echo "Usage: bin/update.sh [OPTIONS]"
            echo ""
            echo "Check for updates and apply them from origin/main."
            echo "Syncs dependencies, runs migrations, and restarts services."
            echo ""
            echo "Options:"
            echo "  --rollback     Revert to previous version on failure"
            echo "  --auto-env     Auto-append new .env.sample keys to .env"
            echo "  --dry-run      Show what would happen without applying"
            echo "  --json         Output as JSON"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

run_update
```

**Step 2: Make executable and verify syntax**

Run: `chmod +x bin/update.sh && bash -n bin/update.sh`
Expected: no output

**Step 3: Smoke test**

Run: `bin/update.sh --help`
Expected: usage text showing all flags

Run: `bin/update.sh --dry-run`
Expected: shows what would happen (fetch, compare, report)

**Step 4: Commit**

```bash
git add bin/update.sh
git commit -m "feat: add bin/update.sh entry point"
```

---

### Task 8: Update setup_cron.sh

**Files:**
- Modify: `bin/setup_cron.sh`

**Step 1: Add auto-update prompt after the health check cron is added**

After line 91 (after the health check cron job is added via `crontab`), add the auto-update prompt:

```bash
# --- Auto-update option ---

echo ""
read -p "Enable automatic updates (pulls from origin/main on same schedule)? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    UPDATE_CMD="cd $PROJECT_DIR && $BIN_DIR/update.sh --rollback --auto-env >> $PROJECT_DIR/update.log 2>&1"
    UPDATE_ID="# server-maintanence auto-update"

    # Remove existing update job if present
    crontab -l 2>/dev/null | grep -v -F "$UPDATE_ID" | crontab -

    # Add update job on same schedule
    (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $UPDATE_CMD $UPDATE_ID") | crontab -

    success "Auto-update cron job added (with --rollback enabled)"
    info "Update log: $PROJECT_DIR/update.log"
fi
```

The update cron uses `--rollback` by default so unattended updates are safe.

**Step 2: Verify syntax**

Run: `bash -n bin/setup_cron.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/setup_cron.sh
git commit -m "feat: add auto-update option to cron setup"
```

---

### Task 9: Write bats tests

**Files:**
- Create: `bin/tests/test_update.bats`

**Step 1: Create bats test file**

```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "update.sh passes syntax check" {
    run bash -n "$BIN_DIR/update.sh"
    assert_success
}

@test "update library passes syntax check" {
    run bash -n "$LIB_DIR/update.sh"
    assert_success
}

@test "update.sh --help shows usage" {
    run "$BIN_DIR/update.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "--rollback"
    assert_output --partial "--dry-run"
    assert_output --partial "--json"
    assert_output --partial "--auto-env"
}

@test "update.sh --dry-run does not modify repo" {
    local sha_before
    sha_before=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    run "$BIN_DIR/update.sh" --dry-run
    local sha_after
    sha_after=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    assert_equal "$sha_before" "$sha_after"
}

@test "update.sh --dry-run --json outputs JSON" {
    run "$BIN_DIR/update.sh" --dry-run --json
    [[ "${output}" == "{"* ]]
}

@test "setup_cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_cron.sh"
    assert_success
}
```

**Step 2: Run the tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_update.bats`
Expected: all tests pass

**Step 3: Commit**

```bash
git add bin/tests/test_update.bats
git commit -m "test: add bats tests for update.sh"
```

---

### Task 10: Update docs

**Files:**
- Modify: `bin/README.md`

**Step 1: Add `sm-update` to the Quick Command Reference table**

Add row: `| \`sm-update\` | — | — | Auto-update from origin/main |`

**Step 2: Add `update.sh` section before the `check_security.sh` section**

```markdown
---

### `update.sh` — Auto-Update

Checks for updates from `origin/main` and applies them. Syncs dependencies, runs migrations, and restarts services based on the detected deployment mode.

\`\`\`bash
# Check and apply updates
./bin/update.sh

# Dry run (show what would happen)
./bin/update.sh --dry-run

# Enable automatic rollback on failure
./bin/update.sh --rollback

# JSON output (for CI or monitoring)
./bin/update.sh --json
\`\`\`

**What it does:**
1. `git fetch origin main` — check for new commits
2. `git pull origin main` — apply changes
3. `uv sync` — sync dependencies (mode-aware)
4. `python manage.py migrate` — apply database migrations
5. Restart services (systemd, docker compose, or skip for dev)
6. Notify on success or failure (best-effort)

**Flags:**
- `--rollback` — revert to previous version if any step fails
- `--auto-env` — auto-append new `.env.sample` keys to `.env`
- `--dry-run` — preview without applying
- `--json` — JSON output

**Exit codes:** `0` = up to date or updated, `1` = error.

**Cron:** Run `./bin/setup_cron.sh` and answer "y" to the auto-update prompt.
```

**Step 3: Also add a note to the `setup_cron.sh` section mentioning the new auto-update option**

In the existing `setup_cron.sh` section of `bin/README.md`, add a bullet under "What it does":
```
- Optionally sets up automatic updates (`bin/update.sh --rollback`)
```

**Step 4: Commit**

```bash
git add bin/README.md
git commit -m "docs: add update.sh to bin/README.md"
```