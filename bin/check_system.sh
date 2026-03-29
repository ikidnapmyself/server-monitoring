#!/bin/bash
#
# System check script for server-maintanence
# Unified health check — auto-detects deployment mode
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/health_check.sh"

cd "$PROJECT_DIR"

# Parse flags
for arg in "$@"; do
    case $arg in
        --json) _hc_json_mode=true ;;
        --help|-h)
            echo "Usage: bin/check_system.sh [OPTIONS]"
            echo ""
            echo "Run health checks for server-maintanence."
            echo "Auto-detects deployment mode (dev/prod/docker/systemd)."
            echo ""
            echo "Options:"
            echo "  --json         Output as JSON"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

run_all_checks