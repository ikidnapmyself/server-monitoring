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