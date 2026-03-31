#!/bin/bash
#
# Docker deployment script for server-maintanence
# Pre-flight checks, build, and deploy via docker compose
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/docker.sh"
source "$SCRIPT_DIR/lib/paths.sh"

COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"
cd "$PROJECT_DIR"

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

# Check 1b: DEPLOY_METHOD consistency
_deploy_method_val=$(grep -E "^DEPLOY_METHOD=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
if [ -z "${_deploy_method_val:-}" ]; then
    # Key absent — write it now so .env is consistent
    echo "DEPLOY_METHOD=docker" >> "$PROJECT_DIR/.env"
    info "DEPLOY_METHOD=docker written to .env"
elif [ "$_deploy_method_val" != "docker" ]; then
    warn ".env has DEPLOY_METHOD=$_deploy_method_val but you are running the Docker deployer."
    warn "Continuing anyway — update DEPLOY_METHOD=docker in .env if this is intentional."
fi

# Check 2 & 3: Docker daemon + compose v2
docker_preflight || exit 1

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
        WEB_STATE=$(get_service_state "$COMPOSE_FILE" web)
        if [ "$WEB_STATE" = "running" ]; then
            WEB_OK=true
            success "Web service is healthy"
        fi
    fi

    # Celery check
    if [ "$CELERY_OK" = false ]; then
        CELERY_STATE=$(get_service_state "$COMPOSE_FILE" celery)
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
        WEB_RECHECK=$(get_service_state "$COMPOSE_FILE" web)
        CELERY_RECHECK=$(get_service_state "$COMPOSE_FILE" celery)

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