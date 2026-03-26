#!/bin/bash
#
# Docker deployment script for server-maintanence
# Pre-flight checks, build, and deploy via docker compose
#

set -e

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Compose file location
COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"

# Change to project directory
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ===========================================
#   Pre-flight checks
# ===========================================

echo ""
echo "============================================"
echo "   Docker Deployment — Pre-flight Checks"
echo "============================================"
echo ""

# Check 1: .env file exists
info "Checking for .env file..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    error ".env file not found."
    echo "  Run ./bin/install.sh first, or copy .env.sample to .env and configure it."
    exit 1
fi
success ".env file found"

# Check 2: Docker daemon is running
info "Checking Docker daemon..."
if ! docker info >/dev/null 2>&1; then
    error "Docker is required. Install it from https://docs.docker.com/get-docker/ and ensure the daemon is running."
    exit 1
fi
success "Docker daemon is running"

# Check 3: docker compose v2 is available
info "Checking docker compose v2..."
if ! docker compose version >/dev/null 2>&1; then
    error "docker compose v2 is required but not available."
    echo "  Docker Compose v2 is included with Docker Desktop, or can be installed as a plugin."
    echo "  See: https://docs.docker.com/compose/install/"
    exit 1
fi
COMPOSE_VERSION="$(docker compose version --short)"
success "docker compose v2 is available (v${COMPOSE_VERSION})"

echo ""
success "All pre-flight checks passed"

# --- Build & Start ---

info "Building Docker images..."
docker compose -f "$COMPOSE_FILE" build
success "Docker images built"

echo ""
info "Starting Docker Compose stack..."
docker compose -f "$COMPOSE_FILE" up -d
success "Docker Compose stack started"

# ===========================================
#   Health Verification
# ===========================================

echo ""
echo "============================================"
echo "   Health Verification"
echo "============================================"
echo ""

info "Verifying stack health (timeout: 60s)..."

TIMEOUT=60
INTERVAL=5
ELAPSED=0
REDIS_OK=false
WEB_OK=false
CELERY_OK=false

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    # Redis check
    if [ "$REDIS_OK" = false ]; then
        if docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
            REDIS_OK=true
            success "Redis is healthy"
        fi
    fi

    # Web check
    if [ "$WEB_OK" = false ]; then
        WEB_STATE=$(docker compose -f "$COMPOSE_FILE" ps --format json web 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || true)
        if [ "$WEB_STATE" = "running" ]; then
            WEB_OK=true
            success "Web service is healthy"
        fi
    fi

    # Celery check
    if [ "$CELERY_OK" = false ]; then
        CELERY_STATE=$(docker compose -f "$COMPOSE_FILE" ps --format json celery 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || true)
        if [ "$CELERY_STATE" = "running" ]; then
            CELERY_OK=true
            success "Celery service is healthy"
        fi
    fi

    # All healthy — confirm not in restart loop
    if [ "$REDIS_OK" = true ] && [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
        info "All services running — confirming stability..."
        sleep "$INTERVAL"

        # Re-check web and celery are still running (catches crash-restart loops)
        WEB_RECHECK=$(docker compose -f "$COMPOSE_FILE" ps --format json web 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || true)
        CELERY_RECHECK=$(docker compose -f "$COMPOSE_FILE" ps --format json celery 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    data = data[0] if data else {}
print(data.get('State', ''))" 2>/dev/null || true)

        if [ "$WEB_RECHECK" != "running" ]; then
            WEB_OK=false
            warn "Web service was running but is now restarting (crash loop detected)"
        fi
        if [ "$CELERY_RECHECK" != "running" ]; then
            CELERY_OK=false
            warn "Celery service was running but is now restarting (crash loop detected)"
        fi

        # If still all OK after recheck, we're good
        if [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
            break
        fi
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

# Report failures
FAILED=false
if [ "$REDIS_OK" = false ]; then
    error "Redis failed to become healthy. Check logs: docker compose -f $COMPOSE_FILE logs redis"
    FAILED=true
fi
if [ "$WEB_OK" = false ]; then
    error "Web service failed to become healthy. Check logs: docker compose -f $COMPOSE_FILE logs web"
    FAILED=true
fi
if [ "$CELERY_OK" = false ]; then
    error "Celery service failed to become healthy. Check logs: docker compose -f $COMPOSE_FILE logs celery"
    FAILED=true
fi

if [ "$FAILED" = true ]; then
    exit 1
fi

echo ""
success "All services are healthy"

# --- Summary ---

echo ""
echo "============================================"
echo -e "${GREEN}   Docker Stack Running!${NC}"
echo "============================================"
echo ""

WEB_PORT=$(grep -E "^WEB_PORT=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2)
WEB_PORT="${WEB_PORT:-8000}"

echo "Services:"
echo "  - Web:    http://localhost:${WEB_PORT}"
echo "  - Redis:  redis://localhost:6379 (internal)"
echo "  - Celery: background worker"
echo ""
echo "Useful commands:"
echo "  docker compose -f $COMPOSE_FILE logs -f       # Follow logs"
echo "  docker compose -f $COMPOSE_FILE ps             # Service status"
echo "  docker compose -f $COMPOSE_FILE down           # Stop stack"
echo "  docker compose -f $COMPOSE_FILE up -d --build  # Rebuild & restart"
echo ""