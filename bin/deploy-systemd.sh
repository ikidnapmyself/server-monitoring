#!/bin/bash
#
# systemd deployment script for server-maintanence
# Installs systemd units, runs migrations, and starts services.
# Assumes install.sh prod mode already handled .env and dependencies.
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/checks.sh"
source "$SCRIPT_DIR/lib/paths.sh"

UNIT_DIR="$PROJECT_DIR/deploy/systemd"
INSTALL_DIR="/opt/server-monitoring"
ENV_FILE="/etc/server-monitoring/env"

# ===========================================
#   Pre-flight checks
# ===========================================

echo ""
echo "============================================"
echo "   systemd Deployment — Pre-flight Checks"
echo "============================================"
echo ""

# Must be root
info "Checking privileges..."
if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi
success "Running as root"

# Project dir exists with .venv
info "Checking installation at $INSTALL_DIR..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    error "$INSTALL_DIR/.venv not found."
    echo "  Run install.sh in prod+bare mode first to set up the project."
    exit 1
fi
success "Installation found at $INSTALL_DIR"

# Environment file exists
info "Checking environment file..."
if [ ! -f "$ENV_FILE" ]; then
    error "$ENV_FILE not found."
    echo "  Create it with your production environment variables."
    echo "  See: docs/Deployment.md"
    exit 1
fi
success "Environment file found"

# DEPLOY_METHOD consistency
_deploy_method_val=$(grep -E "^DEPLOY_METHOD=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)
if [ -z "${_deploy_method_val:-}" ]; then
    # systemd is a service manager within bare-metal deployment, not its own
    # DEPLOY_METHOD value. Write "bare" so the two-axis model stays consistent.
    echo "DEPLOY_METHOD=bare" >> "$ENV_FILE"
    info "DEPLOY_METHOD=bare written to $ENV_FILE"
elif [ "$_deploy_method_val" != "bare" ]; then
    warn "$ENV_FILE has DEPLOY_METHOD=$_deploy_method_val but you are running the systemd deployer."
    warn "Continuing anyway — update DEPLOY_METHOD=bare in $ENV_FILE if this is intentional."
fi

# Redis is running (check both unit names)
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
    exit 1
fi

echo ""
success "All pre-flight checks passed"

# ===========================================
#   Deploy
# ===========================================

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

# ===========================================
#   Health Verification
# ===========================================

echo ""
echo "============================================"
echo "   Health Verification"
echo "============================================"
echo ""

info "Verifying service health (timeout: 60s)..."

TIMEOUT=60
INTERVAL=5
ELAPSED=0
WEB_OK=false
CELERY_OK=false

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
FAILED=false
if [ "$WEB_OK" = false ]; then
    error "server-monitoring failed to start. Check: journalctl -u server-monitoring"
    FAILED=true
fi
if [ "$CELERY_OK" = false ]; then
    error "server-monitoring-celery failed to start. Check: journalctl -u server-monitoring-celery"
    FAILED=true
fi

if [ "$FAILED" = true ]; then
    exit 1
fi

echo ""
success "All services are healthy"

# ===========================================
#   Summary
# ===========================================

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
