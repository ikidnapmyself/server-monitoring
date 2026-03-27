#!/usr/bin/env bash
#
# Docker Compose helpers.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_DOCKER_LOADED:-}" ]] && return 0
_LIB_DOCKER_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/checks.sh"

# Parse the state of a service from docker compose ps JSON output.
# Reads from stdin for testability.
# Usage: echo "$json" | parse_service_state <service_name>
parse_service_state() {
    local service="$1"
    python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    if isinstance(data, list):
        for d in data:
            if d.get('Service') == '$service':
                print(d.get('State', ''))
                sys.exit(0)
    elif isinstance(data, dict):
        if data.get('Service') == '$service':
            print(data.get('State', ''))
            sys.exit(0)
" 2>/dev/null || true
}

# Get the state of a running docker compose service.
# Usage: get_service_state <compose_file> <service_name>
get_service_state() {
    local compose_file="$1"
    local service="$2"
    docker compose -f "$compose_file" ps --format json 2>/dev/null \
        | parse_service_state "$service"
}

# Run Docker pre-flight checks (daemon + compose v2).
# Returns 1 on failure.
docker_preflight() {
    info "Checking Docker daemon..."
    if ! command_exists docker || ! docker info >/dev/null 2>&1; then
        error "Docker is not running."
        echo "  Docker is required. Install it from https://docs.docker.com/get-docker/"
        echo "  and ensure the daemon is running."
        return 1
    fi
    success "Docker daemon is running"

    info "Checking docker compose v2..."
    if ! docker compose version >/dev/null 2>&1; then
        error "docker compose v2 is required but not available."
        echo "  See: https://docs.docker.com/compose/install/"
        return 1
    fi
    local compose_version
    compose_version="$(docker compose version --short)"
    success "docker compose v2 is available (v${compose_version})"
    return 0
}