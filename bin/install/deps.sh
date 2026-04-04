#!/usr/bin/env bash
#
# Installer module: dependency installation via uv.
#
# Reads DEPLOY_METHOD and DJANGO_ENV from .env, then runs the appropriate
# uv sync command.  For docker deployments, deps are container-managed.
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
source "$_LIB_DIR/checks.sh"

_ENV_FILE="$PROJECT_DIR/.env"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   Dependency Installation"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# Docker early-return
# ---------------------------------------------------------------------------

DEPLOY_METHOD="$(dotenv_get "$_ENV_FILE" "DEPLOY_METHOD")"

if [ "$DEPLOY_METHOD" = "docker" ]; then
    info "Dependencies are managed inside the container — skipping."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Python & uv checks
# ---------------------------------------------------------------------------

check_python || { error "Python check failed — cannot install dependencies."; return 1 2>/dev/null || exit 1; }
check_uv     || { error "uv check failed — cannot install dependencies.";     return 1 2>/dev/null || exit 1; }

# ---------------------------------------------------------------------------
# Install dependencies
# ---------------------------------------------------------------------------

DJANGO_ENV="$(dotenv_get "$_ENV_FILE" "DJANGO_ENV")"

if [ "$DJANGO_ENV" = "dev" ]; then
    info "Installing dependencies with uv sync (including development extras)..."
    uv sync --all-extras --dev
else
    info "Installing dependencies with uv sync (production)..."
    uv sync
fi

success "Dependencies installed."

return 0 2>/dev/null || exit 0