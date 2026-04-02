#!/bin/bash
#
# Cron setup script for server-maintanence
# Finds the current directory and adds health check to crontab
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/paths.sh"

cd "$PROJECT_DIR"

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
CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py run_pipeline --checks-only --json >> $PROJECT_DIR/cron.log 2>&1"

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

# --- Auto-update option ---

echo ""
read -p "Enable automatic updates (pulls from origin/main on same schedule)? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    UPDATE_CMD="cd $PROJECT_DIR && $SCRIPT_DIR/update.sh --rollback --auto-env >> $PROJECT_DIR/update.log 2>&1"
    UPDATE_ID="# server-maintanence auto-update"

    # Remove existing update job if present
    crontab -l 2>/dev/null | grep -v -F "$UPDATE_ID" | crontab -

    # Add update job on same schedule
    (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $UPDATE_CMD $UPDATE_ID") | crontab -

    success "Auto-update cron job added (with --rollback enabled)"
    info "Update log: $PROJECT_DIR/update.log"
fi

# --- Cluster push option ---

# Check if HUB_URL is set in .env (agent mode)
_hub_url=""
if [ -f "$PROJECT_DIR/.env" ]; then
    _hub_url=$(grep -E "^HUB_URL=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
fi

if [ -n "$_hub_url" ]; then
    echo ""
    read -p "HUB_URL detected — schedule automatic push to hub? [Y/n] " -n 1 -r
    echo ""

    if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
        PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub --json >> $PROJECT_DIR/push.log 2>&1"
        PUSH_ID="# server-maintanence cluster push"

        # Remove existing push job if present
        crontab -l 2>/dev/null | grep -v -F "$PUSH_ID" | crontab -

        # Add push job on same schedule
        (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $PUSH_CMD $PUSH_ID") | crontab -

        success "Cluster push cron job added"
        info "Push log: $PROJECT_DIR/push.log"
    fi
fi

echo ""
echo "============================================"
echo -e "${GREEN}   Cron Setup Complete!${NC}"
echo "============================================"
echo ""
info "Health checks will run: $CRON_SCHEDULE"
info "Log file: $PROJECT_DIR/cron.log"
if [ -n "${_hub_url:-}" ]; then
    info "Push log: $PROJECT_DIR/push.log"
fi
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

