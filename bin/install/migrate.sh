#!/usr/bin/env bash
#
# Installer module: Django migrations and system checks.
#
# Runs `manage.py migrate` and `manage.py check` for bare-metal deploys.
# Docker deploys skip — migrations run inside the container.
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

# ---------------------------------------------------------------------------
# Read deploy method
# ---------------------------------------------------------------------------

_ENV_FILE="$PROJECT_DIR/.env"
DEPLOY_METHOD="$(dotenv_get "$_ENV_FILE" "DEPLOY_METHOD")"

if [ "$DEPLOY_METHOD" = "docker" ]; then
    info "Migrations run inside the Docker container — skipping."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   Django Migrations & System Checks"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 1. Run migrations
# ---------------------------------------------------------------------------

info "Running database migrations..."
uv run python manage.py migrate
success "Migrations applied."

# ---------------------------------------------------------------------------
# 2. Django system checks
# ---------------------------------------------------------------------------

info "Running Django system checks..."
if uv run python manage.py check; then
    success "All system checks passed."
else
    warn "System checks reported issues (see above). You may want to address them."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

success "Migrations & system checks complete."

return 0 2>/dev/null || exit 0