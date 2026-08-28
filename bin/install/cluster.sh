#!/usr/bin/env bash
#
# Installer module: cluster role configuration.
#
# Configures: INSTANCE_ID, HUB_URL, HUB_API_KEY (+ API_KEY_AUTH_ENABLED for hubs)
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
if dotenv_has_value "$_ENV_FILE" "HUB_URL" || dotenv_has_value "$_ENV_FILE" "HUB_API_KEY"; then
    _cluster_default="default_y"
fi

if ! prompt_yes_no "Configure cluster mode?" "$_cluster_default"; then
    info "Skipping cluster configuration."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# 2. Cluster role (local branching only — not persisted; role is derived at
#    runtime from HUB_URL and whether active API keys exist).
# ---------------------------------------------------------------------------

_role=$(prompt_choice "$_ENV_FILE" "__CLUSTER_MODE_LOCAL" \
    "Select cluster role:" \
    "agent:run checkers locally, push results to a hub" \
    "hub:accept alerts from remote agents" \
    "both:agent + hub on the same instance")

info "Cluster role: $_role"

# ---------------------------------------------------------------------------
# 3. INSTANCE_ID — identity for every role, not just agents.
#
# It keys the Node row for this machine and every alert fingerprint it emits
# (check:{instance_id}:{checker_name}), so a hub that monitors itself needs one
# just as much as an agent that pushes. The default is the hostname plus a short
# random suffix: two stock machines report the same hostname, and a collision
# there would merge two machines' Node rows and alerts into one identity.
#
# prompt_with_default prefers an existing .env value over the default, so
# re-running the installer keeps the id a machine already has.
# ---------------------------------------------------------------------------

INSTANCE_ID=$(prompt_with_default "$_ENV_FILE" "INSTANCE_ID" \
    "INSTANCE_ID" "$(dotenv_default_instance_id)")
dotenv_set "$_ENV_FILE" "INSTANCE_ID" "$INSTANCE_ID"

# ---------------------------------------------------------------------------
# 4. Agent or both: HUB_URL, HUB_API_KEY
# ---------------------------------------------------------------------------

if [ "$_role" = "agent" ] || [ "$_role" = "both" ]; then
    HUB_URL=$(prompt_with_default "$_ENV_FILE" "HUB_URL" \
        "HUB_URL (e.g. https://monitoring-hub.example.com)")
    dotenv_set "$_ENV_FILE" "HUB_URL" "$HUB_URL"

    export PROMPT_MASK=1
    HUB_API_KEY=$(prompt_with_default "$_ENV_FILE" \
        "HUB_API_KEY" \
        "HUB_API_KEY (token created on the hub via create_api_key)")
    unset PROMPT_MASK
    dotenv_set "$_ENV_FILE" "HUB_API_KEY" "$HUB_API_KEY"
fi

# ---------------------------------------------------------------------------
# 5. Hub or both: enable API-key auth and explain key provisioning
# ---------------------------------------------------------------------------

if [ "$_role" = "hub" ] || [ "$_role" = "both" ]; then
    dotenv_set "$_ENV_FILE" "API_KEY_AUTH_ENABLED" "1"
    success "API_KEY_AUTH_ENABLED=1 written to .env (a minted key makes this a hub)"
    echo ""
    info "Provision an API key for each agent and paste it into that agent's HUB_API_KEY:"
    info "    uv run python manage.py create_api_key --name \"<agent-name>\""
    info "The raw token is shown once — copy it immediately."
fi

# ---------------------------------------------------------------------------
# 6. Agent or both: verify with dry-run
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
