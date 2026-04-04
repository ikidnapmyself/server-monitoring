#!/usr/bin/env bash
#
# Installer module: cluster role configuration.
#
# Configures: CLUSTER_ROLE, HUB_URL, INSTANCE_ID,
#             CLUSTER_ENABLED, WEBHOOK_SECRET_CLUSTER
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
# Ensure .env exists
# ---------------------------------------------------------------------------

dotenv_ensure_file
_ENV_FILE="$PROJECT_DIR/.env"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   Cluster Role Configuration"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 1. Ask whether to configure cluster mode
# ---------------------------------------------------------------------------

_cluster_default="default_n"
if dotenv_has_value "$_ENV_FILE" "HUB_URL" || dotenv_has_value "$_ENV_FILE" "CLUSTER_ENABLED"; then
    _cluster_default="default_y"
fi

if ! prompt_yes_no "Configure cluster mode?" "$_cluster_default"; then
    info "Skipping cluster configuration."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# 2. Cluster role
# ---------------------------------------------------------------------------

CLUSTER_ROLE=$(prompt_choice "$_ENV_FILE" "CLUSTER_ROLE" \
    "Select cluster role:" \
    "agent:run checkers locally, push results to a hub" \
    "hub:accept alerts from remote agents" \
    "both:agent + hub on the same instance")

dotenv_set "$_ENV_FILE" "CLUSTER_ROLE" "$CLUSTER_ROLE"
info "Cluster role: $CLUSTER_ROLE"

# ---------------------------------------------------------------------------
# 3. Agent or both: HUB_URL and INSTANCE_ID
# ---------------------------------------------------------------------------

if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    HUB_URL=$(prompt_with_default "$_ENV_FILE" "HUB_URL" \
        "HUB_URL (e.g. https://monitoring-hub.example.com)")
    dotenv_set "$_ENV_FILE" "HUB_URL" "$HUB_URL"

    INSTANCE_ID=$(prompt_with_default "$_ENV_FILE" "INSTANCE_ID" \
        "INSTANCE_ID" "$(hostname 2>/dev/null || echo "")")
    dotenv_set "$_ENV_FILE" "INSTANCE_ID" "$INSTANCE_ID"
fi

# ---------------------------------------------------------------------------
# 4. Hub or both: enable CLUSTER_ENABLED
# ---------------------------------------------------------------------------

if [ "$CLUSTER_ROLE" = "hub" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    dotenv_set "$_ENV_FILE" "CLUSTER_ENABLED" "1"
    success "CLUSTER_ENABLED=1 written to .env"
fi

# ---------------------------------------------------------------------------
# 5. All roles: shared webhook secret
# ---------------------------------------------------------------------------

export PROMPT_MASK=1
WEBHOOK_SECRET_CLUSTER=$(prompt_with_default "$_ENV_FILE" \
    "WEBHOOK_SECRET_CLUSTER" \
    "WEBHOOK_SECRET_CLUSTER (shared secret between agents and hub)")
unset PROMPT_MASK
dotenv_set "$_ENV_FILE" "WEBHOOK_SECRET_CLUSTER" "$WEBHOOK_SECRET_CLUSTER"

# ---------------------------------------------------------------------------
# 6. Agent or both: verify with dry-run
# ---------------------------------------------------------------------------

if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    echo ""
    info "Running push_to_hub --dry-run to verify configuration..."
    if uv run python manage.py push_to_hub --dry-run 2>&1; then
        success "Dry run succeeded — agent is configured correctly"
    else
        warn "Dry run failed — check HUB_URL and try: uv run python manage.py push_to_hub --dry-run"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

success "Cluster configuration complete (role: $CLUSTER_ROLE)."

return 0 2>/dev/null || exit 0