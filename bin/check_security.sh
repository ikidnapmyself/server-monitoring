#!/bin/bash
#
# Security posture audit for server-maintanence
# Auto-detects agent/hub/standalone mode.
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/security_check.sh"

cd "$PROJECT_DIR"

# Parse flags
for arg in "$@"; do
    case $arg in
        --json) _sc_json_mode=true ;;
        --help|-h)
            echo "Usage: bin/check_security.sh [OPTIONS]"
            echo ""
            echo "Audit the security posture of this deployment."
            echo "Auto-detects mode: agent (HUB_URL set), hub (CLUSTER_ENABLED=1), or standalone."
            echo ""
            echo "Options:"
            echo "  --json         Output as JSON"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

run_security_audit