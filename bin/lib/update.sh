#!/usr/bin/env bash
#
# Auto-update library for server-maintanence.
# Handles git pull, dependency sync, migrations, service restart, and rollback.
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
_up_auto_env=false
_up_log_file="$PROJECT_DIR/update.log"
_up_saved_sha=""
_up_new_sha=""
_up_failed_step=""
_up_mode=""

# --- Internal helpers ---

_up_log() {
    local level="$1" msg="$2"
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    printf "[%s] [%s] %s\n" "$ts" "$level" "$msg" >> "$_up_log_file"
    if [ "$_up_json_mode" = false ]; then
        case "$level" in
            INFO)    info "$msg" ;;
            OK)      success "$msg" ;;
            WARN)    warn "$msg" ;;
            ERROR)   error "$msg" ;;
        esac
    fi
}

_up_notify() {
    local title="$1" msg="$2" severity="${3:-info}"

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would notify '$title'"
        return 0
    fi

    if [ ! -d "$PROJECT_DIR/.venv" ] || ! command_exists uv; then
        _up_log "WARN" "Cannot send notification (no .venv or uv)"
        return 0
    fi

    # Best-effort — never let notification failure break the update
    cd "$PROJECT_DIR" && \
        uv run python manage.py test_notify \
            --non-interactive \
            --title "$title" \
            --message "$msg" \
            --severity "$severity" \
        &>/dev/null || true
}

_up_short_sha() {
    printf '%s' "${1:0:7}"
}

_up_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

# --- Update check ---

_up_check_for_updates() {
    _up_log "INFO" "Fetching latest changes from origin..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would run git fetch"
    fi

    if ! git -C "$PROJECT_DIR" fetch origin main &>/dev/null; then
        _up_log "ERROR" "git fetch failed"
        return 1
    fi

    _up_saved_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD)"

    local remote_sha
    remote_sha="$(git -C "$PROJECT_DIR" rev-parse origin/main)"

    if [ "$_up_saved_sha" = "$remote_sha" ]; then
        _up_log "OK" "Already up-to-date at $(_up_short_sha "$_up_saved_sha")"
        return 2
    fi

    local behind
    behind="$(git -C "$PROJECT_DIR" rev-list --count HEAD..origin/main)"
    _up_log "INFO" "Updates available: $behind commit(s) behind origin/main"
    return 0
}

# --- Update steps ---

_up_pull() {
    _up_log "INFO" "Pulling latest changes..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would run git pull origin main"
        _up_new_sha="$_up_saved_sha"
        return 0
    fi

    if ! git -C "$PROJECT_DIR" pull origin main; then
        _up_failed_step="pull"
        _up_log "ERROR" "git pull failed"
        return 1
    fi

    _up_new_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
    _up_log "OK" "Pulled $(_up_short_sha "$_up_saved_sha") -> $(_up_short_sha "$_up_new_sha")"
    return 0
}

_up_sync_env() {
    local sample="$PROJECT_DIR/.env.sample"
    local env_file="$PROJECT_DIR/.env"

    if [ ! -f "$sample" ]; then
        _up_log "INFO" ".env.sample not found, skipping env sync"
        return 0
    fi

    if [ ! -f "$env_file" ]; then
        _up_log "WARN" ".env not found, skipping env sync"
        return 0
    fi

    # Collect keys from .env.sample that are missing in .env
    local missing_keys=()
    while IFS= read -r line; do
        # Skip comments and blank lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$line" ]] && continue

        local key="${line%%=*}"
        if ! grep -q "^${key}=" "$env_file" 2>/dev/null; then
            missing_keys+=("$line")
        fi
    done < "$sample"

    if [ ${#missing_keys[@]} -eq 0 ]; then
        _up_log "OK" ".env is in sync with .env.sample"
        return 0
    fi

    if [ "$_up_auto_env" = true ]; then
        if [ "$_up_dry_run" = true ]; then
            _up_log "INFO" "Dry-run: would append ${#missing_keys[@]} key(s) to .env"
            return 0
        fi

        local date_stamp
        date_stamp="$(date '+%Y-%m-%d')"
        printf "\n# Added by auto-update on %s\n" "$date_stamp" >> "$env_file"
        for entry in "${missing_keys[@]}"; do
            printf "%s\n" "$entry" >> "$env_file"
            _up_log "INFO" "Added to .env: ${entry%%=*}"
        done
        _up_log "OK" "Appended ${#missing_keys[@]} missing key(s) to .env"
    else
        _up_log "WARN" "${#missing_keys[@]} key(s) in .env.sample missing from .env:"
        for entry in "${missing_keys[@]}"; do
            _up_log "WARN" "  ${entry%%=*}"
        done
    fi

    return 0
}

_up_sync_deps() {
    _up_mode="$(detect_mode)"

    if [ "$_up_mode" = "docker" ]; then
        _up_log "INFO" "Docker mode — skipping dependency sync (handled by image build)"
        return 0
    fi

    _up_log "INFO" "Syncing dependencies (mode: $_up_mode)..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would run uv sync"
        return 0
    fi

    local sync_cmd
    if [ "$_up_mode" = "dev" ]; then
        sync_cmd="uv sync --all-extras --dev"
    else
        sync_cmd="uv sync"
    fi

    if ! (cd "$PROJECT_DIR" && $sync_cmd); then
        _up_failed_step="sync_deps"
        _up_log "ERROR" "Dependency sync failed"
        return 1
    fi

    _up_log "OK" "Dependencies synced"
    return 0
}

_up_migrate() {
    if [ "$_up_mode" = "docker" ]; then
        _up_log "INFO" "Docker mode — skipping migrations (handled by entrypoint)"
        return 0
    fi

    _up_log "INFO" "Running database migrations..."

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would run manage.py migrate"
        return 0
    fi

    if ! (cd "$PROJECT_DIR" && uv run python manage.py migrate --no-input); then
        _up_failed_step="migrate"
        _up_log "ERROR" "Database migration failed"
        return 1
    fi

    _up_log "OK" "Migrations applied"
    return 0
}

_up_restart() {
    local compose_file="$PROJECT_DIR/deploy/docker/docker-compose.yml"

    case "$_up_mode" in
        dev)
            _up_log "INFO" "Dev mode — no service restart needed"
            return 0
            ;;
        prod|systemd)
            _up_log "INFO" "Restarting systemd service..."
            if [ "$_up_dry_run" = true ]; then
                _up_log "INFO" "Dry-run: would run systemctl restart server-monitoring"
                return 0
            fi
            if ! systemctl restart server-monitoring; then
                _up_failed_step="restart"
                _up_log "ERROR" "Service restart failed"
                return 1
            fi
            _up_log "OK" "Service restarted"
            ;;
        docker)
            _up_log "INFO" "Rebuilding Docker containers..."
            if [ "$_up_dry_run" = true ]; then
                _up_log "INFO" "Dry-run: would run docker compose up --build -d"
                return 0
            fi
            if ! docker compose -f "$compose_file" up --build -d; then
                _up_failed_step="restart"
                _up_log "ERROR" "Docker compose rebuild failed"
                return 1
            fi
            _up_log "OK" "Docker containers rebuilt"
            ;;
    esac

    return 0
}

# --- Rollback ---

_up_rollback() {
    if [ -z "$_up_saved_sha" ]; then
        _up_log "ERROR" "No saved SHA to rollback to"
        return 1
    fi

    _up_log "WARN" "Rolling back to $(_up_short_sha "$_up_saved_sha")..."

    git -C "$PROJECT_DIR" reset --hard "$_up_saved_sha" || true

    # Re-sync deps, migrate, restart — best-effort
    _up_sync_deps || true
    _up_migrate || true
    _up_restart || true

    _up_log "WARN" "Rollback to $(_up_short_sha "$_up_saved_sha") complete"
}

# --- Orchestrator ---

run_update() {
    _up_mode="$(detect_mode)"

    _up_log "INFO" "Starting update (mode: $_up_mode, dry-run: $_up_dry_run, rollback: $_up_rollback_enabled)"

    # Step 1: Check for updates
    local check_rc=0
    _up_check_for_updates || check_rc=$?

    if [ "$check_rc" -eq 2 ]; then
        # Already up-to-date
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"up-to-date","sha":"%s"}\n' "$(_up_short_sha "$_up_saved_sha")"
        fi
        return 0
    elif [ "$check_rc" -eq 1 ]; then
        # Fetch failed
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"failed","step":"fetch","message":"git fetch failed"}\n'
        fi
        _up_notify "Update Failed" "git fetch failed" "error"
        return 1
    fi

    # Step 2: Pull → sync_env → sync_deps → migrate → restart
    local steps=("_up_pull" "_up_sync_env" "_up_sync_deps" "_up_migrate" "_up_restart")
    local failed=false

    for step in "${steps[@]}"; do
        if ! $step; then
            failed=true
            break
        fi
    done

    if [ "$failed" = true ]; then
        _up_log "ERROR" "Update failed at step: $_up_failed_step"

        if [ "$_up_rollback_enabled" = true ] && [ "$_up_dry_run" = false ]; then
            _up_rollback
        fi

        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"failed","step":"%s","from":"%s","to":"%s"}\n' \
                "$(_up_json_escape "$_up_failed_step")" \
                "$(_up_short_sha "$_up_saved_sha")" \
                "$(_up_short_sha "$_up_new_sha")"
        fi

        _up_notify \
            "Update Failed" \
            "Step '$_up_failed_step' failed while updating from $(_up_short_sha "$_up_saved_sha") to $(_up_short_sha "$_up_new_sha")" \
            "error"
        return 1
    fi

    # Success
    _up_log "OK" "Update complete: $(_up_short_sha "$_up_saved_sha") -> $(_up_short_sha "$_up_new_sha")"

    if [ "$_up_json_mode" = true ]; then
        printf '{"status":"success","from":"%s","to":"%s"}\n' \
            "$(_up_short_sha "$_up_saved_sha")" \
            "$(_up_short_sha "$_up_new_sha")"
    fi

    _up_notify \
        "Update Succeeded" \
        "Updated from $(_up_short_sha "$_up_saved_sha") to $(_up_short_sha "$_up_new_sha")" \
        "info"

    return 0
}