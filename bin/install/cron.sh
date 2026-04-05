#!/usr/bin/env bash
#
# Installer module: cron job configuration.
#
# Sets up health-check cron, optional auto-update, and optional cluster push.
#
# Source this file from install.sh, or run directly for standalone use.
#

# ---------------------------------------------------------------------------
# Bootstrap paths and dependencies
# ---------------------------------------------------------------------------

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"
_BIN_DIR="$(cd "$_INSTALL_DIR/.." && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/prompt.sh"

_ENV_FILE="$PROJECT_DIR/.env"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   Cron Setup"
echo "============================================"
echo ""

info "Project directory: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# Check uv is available
# ---------------------------------------------------------------------------

if ! command -v uv &> /dev/null; then
    error "uv is not installed. Please run the installer first."
    return 1 2>/dev/null || exit 1
fi

UV_PATH=$(which uv)

# ---------------------------------------------------------------------------
# 1. Schedule selection
# ---------------------------------------------------------------------------

CRON_SCHEDULE=$(prompt_choice "$_ENV_FILE" "CRON_SCHEDULE" \
    "Select cron schedule:" \
    "*/5 * * * *:Every 5 minutes" \
    "*/15 * * * *:Every 15 minutes" \
    "0 * * * *:Every hour" \
    "0 */6 * * *:Every 6 hours" \
    "0 0 * * *:Daily at midnight" \
    "custom:Custom schedule")

if [[ "$CRON_SCHEDULE" == "custom" ]]; then
    CRON_SCHEDULE=$(prompt_with_default "$_ENV_FILE" "CRON_SCHEDULE" \
        "Enter custom cron schedule (e.g. '*/10 * * * *')" "*/5 * * * *")
fi

export CRON_SCHEDULE
info "Using schedule: $CRON_SCHEDULE"

# ---------------------------------------------------------------------------
# 2. Build cron command
# ---------------------------------------------------------------------------

CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py run_pipeline --checks-only --json >> ${LOG_DIR:-$PROJECT_DIR/logs}/cron.log 2>&1"
CRON_ID="# server-maintanence health check"

# ---------------------------------------------------------------------------
# 3. Check for existing cron job, replace if found
# ---------------------------------------------------------------------------

EXISTING_CRON=$(crontab -l 2>/dev/null | grep -F "$CRON_ID" || true)

if [ -n "$EXISTING_CRON" ]; then
    warn "Existing cron job found. It will be replaced."
    crontab -l 2>/dev/null | grep -v -F "$CRON_ID" | grep -v -F "server-maintanence" | crontab -
fi

# ---------------------------------------------------------------------------
# 4. Add new cron job
# ---------------------------------------------------------------------------

(crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $CRON_CMD $CRON_ID") | crontab -

success "Cron job added successfully!"

# ---------------------------------------------------------------------------
# 5. Auto-update option
# ---------------------------------------------------------------------------

if prompt_yes_no "Enable automatic updates?"; then
    UPDATE_CMD="cd $PROJECT_DIR && $_BIN_DIR/update.sh --rollback --auto-env >> ${LOG_DIR:-$PROJECT_DIR/logs}/update.log 2>&1"
    UPDATE_ID="# server-maintanence auto-update"

    # Remove existing update job if present
    crontab -l 2>/dev/null | grep -v -F "$UPDATE_ID" | crontab -

    # Add update job on same schedule
    (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $UPDATE_CMD $UPDATE_ID") | crontab -

    success "Auto-update cron job added (with --rollback enabled)"
    info "Update log: ${LOG_DIR:-$PROJECT_DIR/logs}/update.log"
    export CRON_AUTO_UPDATE=1
else
    export CRON_AUTO_UPDATE=0
fi

# ---------------------------------------------------------------------------
# 6. Cluster push option (only if HUB_URL is set)
# ---------------------------------------------------------------------------

_hub_url=""
if [ -f "$_ENV_FILE" ]; then
    _hub_url="$(dotenv_get "$_ENV_FILE" "HUB_URL")"
fi

if [ -n "$_hub_url" ]; then
    if prompt_yes_no "Schedule automatic push to hub?" "default_y"; then
        PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub --json >> ${LOG_DIR:-$PROJECT_DIR/logs}/push.log 2>&1"
        PUSH_ID="# server-maintanence cluster push"

        # Remove existing push job if present
        crontab -l 2>/dev/null | grep -v -F "$PUSH_ID" | crontab -

        # Add push job on same schedule
        (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $PUSH_CMD $PUSH_ID") | crontab -

        success "Cluster push cron job added"
        info "Push log: ${LOG_DIR:-$PROJECT_DIR/logs}/push.log"
        export CRON_PUSH_TO_HUB=1
    else
        export CRON_PUSH_TO_HUB=0
    fi
else
    export CRON_PUSH_TO_HUB=0
fi

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo -e "${GREEN}   Cron Setup Complete!${NC}"
echo "============================================"
echo ""
info "Health checks will run: $CRON_SCHEDULE"
info "Log file: ${LOG_DIR:-$PROJECT_DIR/logs}/cron.log"
if [ -n "${_hub_url:-}" ]; then
    info "Push log: ${LOG_DIR:-$PROJECT_DIR/logs}/push.log"
fi
echo ""
echo "Useful commands:"
echo "  - View current crontab:  crontab -l"
echo "  - Edit crontab:          crontab -e"
echo "  - View logs:             tail -f ${LOG_DIR:-$PROJECT_DIR/logs}/cron.log"
echo "  - Remove cron job:       Run this script and choose to remove"
echo ""

# ---------------------------------------------------------------------------
# 8. Optionally view crontab
# ---------------------------------------------------------------------------

if prompt_yes_no "View current crontab?"; then
    echo ""
    info "Current crontab:"
    crontab -l
fi

success "Done!"

return 0 2>/dev/null || exit 0