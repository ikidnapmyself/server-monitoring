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
_up_check_only=false

for arg in "$@"; do
    case $arg in
        --check) _up_check_only=true ;;
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
            echo "  --check        Check for updates without applying"
            echo "  --rollback     Revert to previous version on failure"
            echo "  --auto-env     Auto-append new .env.sample keys to .env"
            echo "  --dry-run      Show what would happen without applying"
            echo "  --json         Output as JSON"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

if [ "$_up_check_only" = true ]; then
    _up_mode="$(detect_mode)"
    _up_check_for_updates
    rc=$?
    if [ "$rc" -eq 2 ]; then
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"up_to_date","sha":"%s"}\n' "$(_up_short_sha "$_up_saved_sha")"
        fi
        exit 0
    elif [ "$rc" -eq 1 ]; then
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"failed","step":"fetch","message":"git fetch failed"}\n'
        fi
        exit 1
    else
        behind="$(git -C "$PROJECT_DIR" rev-list --count HEAD..origin/main)"
        if [ "$_up_json_mode" = true ]; then
            printf '{"status":"updates_available","behind":%s,"current_sha":"%s"}\n' "$behind" "$(_up_short_sha "$_up_saved_sha")"
        fi
        exit 0
    fi
fi

run_update