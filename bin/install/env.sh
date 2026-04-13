#!/usr/bin/env bash
#
# Installer module: environment and core .env configuration.
#
# Configures: DJANGO_ENV, DEPLOY_METHOD, DJANGO_DEBUG,
#             DJANGO_ALLOWED_HOSTS, DJANGO_SECRET_KEY,
#             API_KEY_AUTH_ENABLED
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
echo "   Environment & Core .env Setup"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# 1. DJANGO_ENV
# ---------------------------------------------------------------------------

DJANGO_ENV=$(prompt_choice "$_ENV_FILE" "DJANGO_ENV" \
    "Select environment:" \
    "dev:development (DEBUG=1, eager tasks)" \
    "prod:production (DEBUG=0, real secret key)")

dotenv_set "$_ENV_FILE" "DJANGO_ENV" "$DJANGO_ENV"
info "Environment: $DJANGO_ENV"

# ---------------------------------------------------------------------------
# 2. DEPLOY_METHOD
# ---------------------------------------------------------------------------

DEPLOY_METHOD=$(prompt_choice "$_ENV_FILE" "DEPLOY_METHOD" \
    "Select deployment method:" \
    "bare:bare-metal (Python + uv, systemd or runserver)" \
    "docker:Docker Compose stack (requires Docker running)")

dotenv_set "$_ENV_FILE" "DEPLOY_METHOD" "$DEPLOY_METHOD"
info "Deployment method: $DEPLOY_METHOD"

# ---------------------------------------------------------------------------
# 3. DJANGO_DEBUG
# ---------------------------------------------------------------------------

if [ "$DJANGO_ENV" = "prod" ] && [ "$DEPLOY_METHOD" = "bare" ]; then
    dotenv_set "$_ENV_FILE" "DJANGO_DEBUG" "0"
    info "DJANGO_DEBUG=0 (forced for production bare-metal)"
else
    local_default="1"
    [ "$DJANGO_ENV" = "prod" ] && local_default="0"

    DJANGO_DEBUG=$(prompt_with_default "$_ENV_FILE" "DJANGO_DEBUG" \
        "DJANGO_DEBUG (1=on, 0=off)" "$local_default")

    dotenv_set "$_ENV_FILE" "DJANGO_DEBUG" "$DJANGO_DEBUG"
fi

# ---------------------------------------------------------------------------
# 4. DJANGO_ALLOWED_HOSTS
# ---------------------------------------------------------------------------

if [ "$DJANGO_ENV" = "prod" ]; then
    ALLOWED_HOSTS=$(prompt_with_default "$_ENV_FILE" "DJANGO_ALLOWED_HOSTS" \
        "DJANGO_ALLOWED_HOSTS (comma-separated, e.g. example.com,www.example.com)")
else
    ALLOWED_HOSTS=$(prompt_with_default "$_ENV_FILE" "DJANGO_ALLOWED_HOSTS" \
        "DJANGO_ALLOWED_HOSTS (comma-separated)" "localhost,127.0.0.1")
fi

dotenv_set "$_ENV_FILE" "DJANGO_ALLOWED_HOSTS" "$ALLOWED_HOSTS"

# ---------------------------------------------------------------------------
# 5. DJANGO_SECRET_KEY
# ---------------------------------------------------------------------------

_configure_secret_key() {
    local need_key=true

    # If already set, ask whether to regenerate
    if dotenv_has_value "$_ENV_FILE" "DJANGO_SECRET_KEY"; then
        info "DJANGO_SECRET_KEY is already set."
        if prompt_yes_no "Regenerate?"; then
            need_key=true
        else
            need_key=false
        fi
    fi

    if [ "$need_key" = false ]; then
        return 0
    fi

    # Ask whether to generate automatically
    if prompt_yes_no "Generate DJANGO_SECRET_KEY automatically?" "default_y"; then
        if command_exists python3; then
            local key
            key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
            dotenv_set "$_ENV_FILE" "DJANGO_SECRET_KEY" "$key"
            success "DJANGO_SECRET_KEY generated and saved."
            return 0
        else
            warn "python3 not available; cannot auto-generate."
        fi
    fi

    # Manual entry path
    if [ "$DJANGO_ENV" = "prod" ]; then
        local pasted
        export PROMPT_MASK=1
        pasted=$(prompt_with_default "$_ENV_FILE" "DJANGO_SECRET_KEY" \
            "Paste DJANGO_SECRET_KEY")
        unset PROMPT_MASK
        dotenv_set "$_ENV_FILE" "DJANGO_SECRET_KEY" "$pasted"
        success "DJANGO_SECRET_KEY saved."
    else
        warn "DJANGO_SECRET_KEY not set. Set it manually before production use."
    fi
}

_configure_secret_key

# ---------------------------------------------------------------------------
# 6. API_KEY_AUTH_ENABLED
# ---------------------------------------------------------------------------

if [ "$DJANGO_ENV" = "dev" ]; then
    dotenv_set "$_ENV_FILE" "API_KEY_AUTH_ENABLED" "0"
    info "API_KEY_AUTH_ENABLED=0 (disabled for development)"
else
    if ! dotenv_has_value "$_ENV_FILE" "API_KEY_AUTH_ENABLED"; then
        dotenv_set "$_ENV_FILE" "API_KEY_AUTH_ENABLED" "1"
        info "API_KEY_AUTH_ENABLED=1 (enabled for production)"
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

success "Environment & core .env setup complete."

return 0 2>/dev/null || exit 0