#!/usr/bin/env bash
#
# Installer module: cluster role configuration.
#
# Configures: HUB_URL, INSTANCE_ID, HUB_API_KEY, CLUSTER_ENABLED
#
# Auth is a single API key sent as `Authorization: Bearer HUB_API_KEY`. An agent
# stores the token; a hub mints it with `manage.py create_api_key` and enables the
# API-key middleware. There is no shared HMAC secret.
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
# 2. Cluster role (local branching only — not persisted; role is derived from
#    HUB_URL + CLUSTER_ENABLED at runtime).
# ---------------------------------------------------------------------------

_role=$(prompt_choice "$_ENV_FILE" "__CLUSTER_MODE_LOCAL" \
    "Select cluster role:" \
    "agent:run checkers locally, push results to a hub" \
    "hub:accept alerts from remote agents" \
    "both:agent + hub on the same instance")

info "Cluster role: $_role"

# ---------------------------------------------------------------------------
# 3. Agent or both: HUB_URL, INSTANCE_ID, HUB_API_KEY
# ---------------------------------------------------------------------------

if [ "$_role" = "agent" ] || [ "$_role" = "both" ]; then
    HUB_URL=$(prompt_with_default "$_ENV_FILE" "HUB_URL" \
        "HUB_URL (e.g. https://monitoring-hub.example.com)")
    dotenv_set "$_ENV_FILE" "HUB_URL" "$HUB_URL"

    INSTANCE_ID=$(prompt_with_default "$_ENV_FILE" "INSTANCE_ID" \
        "INSTANCE_ID" "$(hostname 2>/dev/null || echo "")")
    dotenv_set "$_ENV_FILE" "INSTANCE_ID" "$INSTANCE_ID"

    export PROMPT_MASK=1
    HUB_API_KEY=$(prompt_with_default "$_ENV_FILE" \
        "HUB_API_KEY" \
        "HUB_API_KEY (token created on the hub via create_api_key)")
    unset PROMPT_MASK
    dotenv_set "$_ENV_FILE" "HUB_API_KEY" "$HUB_API_KEY"
fi

# ---------------------------------------------------------------------------
# 4. Hub or both: enable CLUSTER_ENABLED and explain key provisioning
# ---------------------------------------------------------------------------

if [ "$_role" = "hub" ] || [ "$_role" = "both" ]; then
    dotenv_set "$_ENV_FILE" "CLUSTER_ENABLED" "1"
    dotenv_set "$_ENV_FILE" "API_KEY_AUTH_ENABLED" "1"
    success "CLUSTER_ENABLED=1 and API_KEY_AUTH_ENABLED=1 written to .env"
    echo ""
    info "Provision an API key for each agent and paste it into that agent's HUB_API_KEY:"
    info "    uv run python manage.py create_api_key --name \"<agent-name>\""
    info "The raw token is shown once — copy it immediately."
fi

# ---------------------------------------------------------------------------
# 5. Agent or both: verify with dry-run
# ---------------------------------------------------------------------------

if [ "$_role" = "agent" ] || [ "$_role" = "both" ]; then
    echo ""
    info "Running push_to_hub --dry-run to verify configuration..."
    if "$UV_BIN" run python manage.py push_to_hub --dry-run 2>&1; then
        success "Dry run succeeded — agent is configured correctly"
    else
        warn "Dry run failed — check HUB_URL and try: uv run python manage.py push_to_hub --dry-run"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

success "Cluster configuration complete (role: $_role)."

return 0 2>/dev/null || exit 0
