#!/bin/bash
#
# Installer script for server-maintanence
# This script handles all setup steps according to README.md
#

set -e

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to project directory
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

echo ""
echo "============================================"
echo "   server-maintanence Installer"
echo "============================================"
echo ""

# Step 1: Check Python version
info "Checking Python version..."
if command_exists python3; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        success "Python $PYTHON_VERSION found (>= 3.10 required)"
    else
        error "Python 3.10+ is required, but found Python $PYTHON_VERSION"
        exit 1
    fi
else
    error "Python 3 is not installed. Please install Python 3.10 or higher."
    exit 1
fi

# Step 2: Check/Install uv
info "Checking for uv package manager..."
if command_exists uv; then
    UV_VERSION=$(uv --version 2>/dev/null | head -n1)
    success "uv is already installed: $UV_VERSION"
else
    warn "uv is not installed. Installing uv..."

    if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh

        # Source the shell profile to get uv in PATH
        if [ -f "$HOME/.cargo/env" ]; then
            source "$HOME/.cargo/env"
        fi

        # Add to current session PATH
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

        if command_exists uv; then
            success "uv installed successfully"
        else
            error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
            exit 1
        fi
    else
        error "Unsupported OS. Please install uv manually: https://docs.astral.sh/uv/"
        exit 1
    fi
fi

# Step 3: Sync dependencies
info "Installing dependencies with uv sync..."
uv sync
success "Dependencies installed"

# Step 4: Run migrations
info "Running database migrations..."
uv run python manage.py migrate
success "Migrations applied"

# Step 5: Run Django system checks
info "Running Django system checks..."
if uv run python manage.py check; then
    success "All system checks passed"
else
    warn "System checks reported issues (see above). You may want to address them."
fi

# Step 6: Summary and next steps
echo ""
echo "============================================"
echo -e "${GREEN}   Installation Complete!${NC}"
echo "============================================"
echo ""
info "Your server-maintanence project is now set up."
echo ""
echo "Quick commands:"
echo "  - Run health checks:    uv run python manage.py check_health"
echo "  - List checkers:        uv run python manage.py check_health --list"
echo "  - Start server:         uv run python manage.py runserver"
echo "  - Run tests:            uv run pytest"
echo ""
echo "Documentation:"
echo "  - Main README:          README.md"
echo "  - Checkers docs:        apps/checkers/README.md"
echo "  - Alerts docs:          apps/alerts/README.md"
echo "  - Notify docs:          apps/notify/README.md"
echo "  - Agent conventions:    agents.md"
echo ""

# Optional: Ask if user wants to run health check
read -p "Would you like to run the health check suite now? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    info "Running health checks..."
    uv run python manage.py check_health
fi

# Optional: Ask if user wants to set up cron
echo ""
read -p "Would you like to set up automatic health checks via cron? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    "$SCRIPT_DIR/setup_cron.sh"
fi

success "Setup complete!"

