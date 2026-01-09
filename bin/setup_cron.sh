#!/bin/bash
#
# Cron setup script for server-maintanence
# Finds the current directory and adds health check to crontab
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "============================================"
echo "   server-maintanence Cron Setup"
echo "============================================"
echo ""

info "Project directory: $PROJECT_DIR"

# Check if uv is available
if ! command -v uv &> /dev/null; then
    error "uv is not installed. Please run ./install.sh first."
    exit 1
fi

# Default schedule: every 5 minutes
DEFAULT_SCHEDULE="*/5 * * * *"

echo ""
echo "Select cron schedule:"
echo "  1) Every 5 minutes (default)"
echo "  2) Every 15 minutes"
echo "  3) Every hour"
echo "  4) Every 6 hours"
echo "  5) Daily at midnight"
echo "  6) Custom schedule"
echo ""

read -p "Enter choice [1-6] (default: 1): " choice

case $choice in
    2)
        CRON_SCHEDULE="*/15 * * * *"
        ;;
    3)
        CRON_SCHEDULE="0 * * * *"
        ;;
    4)
        CRON_SCHEDULE="0 */6 * * *"
        ;;
    5)
        CRON_SCHEDULE="0 0 * * *"
        ;;
    6)
        read -p "Enter custom cron schedule (e.g., '*/10 * * * *'): " CRON_SCHEDULE
        if [ -z "$CRON_SCHEDULE" ]; then
            error "No schedule provided. Using default."
            CRON_SCHEDULE="$DEFAULT_SCHEDULE"
        fi
        ;;
    *)
        CRON_SCHEDULE="$DEFAULT_SCHEDULE"
        ;;
esac

info "Using schedule: $CRON_SCHEDULE"

# Build the cron command
# Use full path to uv and manage.py
UV_PATH=$(which uv)
CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py check_and_alert --json >> $PROJECT_DIR/cron.log 2>&1"

# Create a unique identifier for this cron job
CRON_ID="# server-maintanence health check"

# Check if cron job already exists
EXISTING_CRON=$(crontab -l 2>/dev/null | grep -F "$CRON_ID" || true)

if [ -n "$EXISTING_CRON" ]; then
    warn "Existing cron job found. It will be replaced."
    # Remove existing job
    crontab -l 2>/dev/null | grep -v -F "$CRON_ID" | grep -v -F "server-maintanence" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $CRON_CMD $CRON_ID") | crontab -

success "Cron job added successfully!"

echo ""
echo "============================================"
echo -e "${GREEN}   Cron Setup Complete!${NC}"
echo "============================================"
echo ""
info "Health checks will run: $CRON_SCHEDULE"
info "Log file: $PROJECT_DIR/cron.log"
echo ""
echo "Useful commands:"
echo "  - View current crontab:  crontab -l"
echo "  - Edit crontab:          crontab -e"
echo "  - View logs:             tail -f $PROJECT_DIR/cron.log"
echo "  - Remove cron job:       Run this script and choose to remove"
echo ""

# Ask if user wants to see current crontab
read -p "Would you like to view the current crontab? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    info "Current crontab:"
    crontab -l
fi

success "Done!"

