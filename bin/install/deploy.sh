#!/usr/bin/env bash
#
# Installer module: deployment.
#
# Reads DEPLOY_METHOD and DJANGO_ENV from .env, then:
#   - docker       → build & start Docker Compose stack, health-check
#   - bare + prod  → install systemd units, migrate, start services
#   - bare + dev   → info message, optional health-check suite
#
# Source this file from install.sh, or run directly for standalone use.
#

# ---------------------------------------------------------------------------
# Bootstrap paths and dependencies
# ---------------------------------------------------------------------------

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/prompt.sh"
source "$_LIB_DIR/checks.sh"

# ---------------------------------------------------------------------------
# Read configuration from .env
# ---------------------------------------------------------------------------

_ENV_FILE="$PROJECT_DIR/.env"

DEPLOY_METHOD="$(dotenv_get "$_ENV_FILE" "DEPLOY_METHOD")"
DJANGO_ENV="$(dotenv_get "$_ENV_FILE" "DJANGO_ENV")"

if [ -z "$DEPLOY_METHOD" ]; then
    error "DEPLOY_METHOD is not set in .env. Run the environment setup first."
    return 1 2>/dev/null || exit 1
fi

# ---------------------------------------------------------------------------
# Docker path
# ---------------------------------------------------------------------------

_deploy_docker() {
    source "$_LIB_DIR/docker.sh"

    local COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"

    echo ""
    echo "============================================"
    echo "   Docker Deployment — Pre-flight Checks"
    echo "============================================"
    echo ""

    # Check 1: .env file exists
    info "Checking for .env file..."
    if [ ! -f "$_ENV_FILE" ]; then
        error ".env file not found."
        echo "  Run ./bin/install.sh first, or copy .env.sample to .env and configure it."
        return 1
    fi
    success ".env file found"

    # Check 2: DEPLOY_METHOD consistency
    local _dm
    _dm="$(dotenv_get "$_ENV_FILE" "DEPLOY_METHOD")"
    if [ -z "$_dm" ]; then
        dotenv_set "$_ENV_FILE" "DEPLOY_METHOD" "docker"
        info "DEPLOY_METHOD=docker written to .env"
    elif [ "$_dm" != "docker" ]; then
        warn ".env has DEPLOY_METHOD=$_dm but you are running the Docker deployer."
        warn "Continuing anyway — update DEPLOY_METHOD=docker in .env if this is intentional."
    fi

    # Check 3: Docker daemon + compose v2
    docker_preflight || return 1

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

    # --- Health Verification ---

    echo ""
    echo "============================================"
    echo "   Health Verification"
    echo "============================================"
    echo ""

    info "Verifying stack health (timeout: 60s)..."

    local TIMEOUT=60
    local INTERVAL=5
    local ELAPSED=0
    local REDIS_OK=false
    local WEB_OK=false
    local CELERY_OK=false

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
            local WEB_STATE
            WEB_STATE=$(get_service_state "$COMPOSE_FILE" web)
            if [ "$WEB_STATE" = "running" ]; then
                WEB_OK=true
                success "Web service is healthy"
            fi
        fi

        # Celery check
        if [ "$CELERY_OK" = false ]; then
            local CELERY_STATE
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

            local WEB_RECHECK CELERY_RECHECK
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

            if [ "$WEB_OK" = true ] && [ "$CELERY_OK" = true ]; then
                break
            fi
        fi

        sleep "$INTERVAL"
        ELAPSED=$((ELAPSED + INTERVAL))
    done

    # Report failures
    local FAILED=false
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
        return 1
    fi

    echo ""
    success "All services are healthy"

    # --- Summary ---

    local WEB_PORT
    WEB_PORT=$(dotenv_get "$_ENV_FILE" "WEB_PORT")
    WEB_PORT="${WEB_PORT:-8000}"

    echo ""
    echo "============================================"
    echo -e "${GREEN}   Docker Stack Running!${NC}"
    echo "============================================"
    echo ""
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

    return 0
}

# ---------------------------------------------------------------------------
# Bare-metal + production path (systemd)
# ---------------------------------------------------------------------------

_deploy_bare_prod() {
    local INSTALL_DIR="/opt/server-monitoring"
    local ENV_FILE="/etc/server-monitoring/env"
    local UNIT_DIR="$PROJECT_DIR/deploy/systemd"

    echo ""
    echo "============================================"
    echo "   systemd Deployment — Pre-flight Checks"
    echo "============================================"
    echo ""

    # Must be root
    info "Checking privileges..."
    if [ "$(id -u)" -ne 0 ]; then
        error "This script must be run as root (or with sudo)."
        return 1
    fi
    success "Running as root"

    # Project dir exists with .venv
    info "Checking installation at $INSTALL_DIR..."
    if [ ! -d "$INSTALL_DIR/.venv" ]; then
        error "$INSTALL_DIR/.venv not found."
        echo "  Run install.sh in prod+bare mode first to set up the project."
        return 1
    fi
    success "Installation found at $INSTALL_DIR"

    # Environment file exists
    info "Checking environment file..."
    if [ ! -f "$ENV_FILE" ]; then
        error "$ENV_FILE not found."
        echo "  Create it with your production environment variables."
        echo "  See: docs/Deployment.md"
        return 1
    fi
    success "Environment file found"

    # DEPLOY_METHOD consistency
    local _dm
    _dm=$(grep -E "^DEPLOY_METHOD=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)
    if [ -z "${_dm:-}" ]; then
        echo "DEPLOY_METHOD=bare" >> "$ENV_FILE"
        info "DEPLOY_METHOD=bare written to $ENV_FILE"
    elif [ "$_dm" != "bare" ]; then
        warn "$ENV_FILE has DEPLOY_METHOD=$_dm but you are running the systemd deployer."
        warn "Continuing anyway — update DEPLOY_METHOD=bare in $ENV_FILE if this is intentional."
    fi

    # Redis is running
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
        return 1
    fi

    echo ""
    success "All pre-flight checks passed"

    # --- Deploy ---

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

    # --- Health Verification ---

    echo ""
    echo "============================================"
    echo "   Health Verification"
    echo "============================================"
    echo ""

    info "Verifying service health (timeout: 60s)..."

    local TIMEOUT=60
    local INTERVAL=5
    local ELAPSED=0
    local WEB_OK=false
    local CELERY_OK=false

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
    local FAILED=false
    if [ "$WEB_OK" = false ]; then
        error "server-monitoring failed to start. Check: journalctl -u server-monitoring"
        FAILED=true
    fi
    if [ "$CELERY_OK" = false ]; then
        error "server-monitoring-celery failed to start. Check: journalctl -u server-monitoring-celery"
        FAILED=true
    fi

    if [ "$FAILED" = true ]; then
        return 1
    fi

    echo ""
    success "All services are healthy"

    # --- Summary ---

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

    return 0
}

# ---------------------------------------------------------------------------
# Bare-metal + dev path
# ---------------------------------------------------------------------------

_deploy_bare_dev() {
    echo ""
    info "Dev mode — start the server with: uv run python manage.py runserver"
    echo ""

    if prompt_yes_no "Run health check suite now?"; then
        info "Running health checks..."
        cd "$PROJECT_DIR" && uv run python manage.py check_health
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Route to the appropriate deploy path
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   Deployment"
echo "============================================"
echo ""

info "DEPLOY_METHOD=$DEPLOY_METHOD  DJANGO_ENV=$DJANGO_ENV"

case "$DEPLOY_METHOD" in
    docker)
        _deploy_docker
        ;;
    bare)
        if [ "$DJANGO_ENV" = "prod" ]; then
            _deploy_bare_prod
        else
            _deploy_bare_dev
        fi
        ;;
    *)
        error "Unknown DEPLOY_METHOD: $DEPLOY_METHOD"
        return 1 2>/dev/null || exit 1
        ;;
esac

return 0 2>/dev/null || exit 0