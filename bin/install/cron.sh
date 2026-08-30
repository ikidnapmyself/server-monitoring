#!/usr/bin/env bash
#
# Installer module: cron job configuration.
#
# Sets up health-check cron, optional auto-update, and — for an agent with a
# HUB_URL — a recurring push to its hub.
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

if [ -z "$UV_BIN" ]; then
    error "uv is not installed (no absolute uv found). Please run the installer first."
    return 1 2>/dev/null || exit 1
fi

# Bake the absolute uv path into the crontab line so cron (a minimal-PATH,
# non-login environment) never depends on PATH resolution.
UV_PATH="$UV_BIN"

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

# ``check_health`` is the local entrypoint: it runs this machine's checkers,
# records their alerts, enqueues one run per materially changed incident and
# drains those runs before returning. That is the whole job a cron-only install
# needs, with no daemon draining an inbox. It replaces the deprecated
# ``run_pipeline --checks-only``, which does the same work but now prints a
# deprecation notice into cron.log on every tick.
#
# ``--json`` carries over unchanged. The exit code does not: check_health exits
# 2 on a CRITICAL result and 1 on UNKNOWN, where the old command exited 0
# unless the pipeline itself failed. Cron ignores the status and all output is
# redirected here, so nothing changes for the scheduled job — but a wrapper
# that inspects `$?` would see it.
CRON_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py check_health --json >> ${LOG_DIR:-$PROJECT_DIR/logs}/cron.log 2>&1"
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
# 6. Agents only: also push results to the hub
# ---------------------------------------------------------------------------

# A machine with no HUB_URL needs nothing here. The health-check job above is
# already its self-monitoring: check_health writes the alerts, lets incidents
# form, and drains the run each materially changed incident earns — the same
# lanes and executors webhook traffic gets, just synchronously. A second local
# job would produce the same alert on the same schedule, and its PENDING run
# would need a process_inbox that a cron-only install does not have.

_hub_url=""
if [ -f "$_ENV_FILE" ]; then
    _hub_url="$(dotenv_get "$_ENV_FILE" "HUB_URL")"
fi

if [ -n "$_hub_url" ]; then
    if prompt_yes_no "Schedule automatic push to hub?" "default_y"; then
        PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub >> ${LOG_DIR:-$PROJECT_DIR/logs}/push.log 2>&1"
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
if [ "${CRON_PUSH_TO_HUB:-0}" = "1" ]; then
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