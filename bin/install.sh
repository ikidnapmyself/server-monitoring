#!/bin/bash
#
# Installer script for server-maintanence
# This script handles all setup steps according to README.md
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/checks.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"

cd "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# .env setup — unified for all environment / deployment-method combinations
#
# Arguments:
#   $1  DJANGO_ENV    — "dev" or "prod"
#   $2  DEPLOY_METHOD — "bare" or "docker"
# ---------------------------------------------------------------------------

dotenv_prompt_setup() {
    local django_env="$1"
    local deploy_method="$2"
    local env_file="$PROJECT_DIR/.env"

    echo ""
    echo "============================================"
    echo "   Environment setup (.env)"
    echo "============================================"
    echo ""
    info "This will append missing values to .env (it will not overwrite existing entries)."
    info "Configuring .env for env=$django_env / method=$deploy_method."

    # Write the two primary axes
    dotenv_set_if_missing "$env_file" "DJANGO_ENV" "$django_env"
    dotenv_set_if_missing "$env_file" "DEPLOY_METHOD" "$deploy_method"

    # ------------------------------------------------------------------
    # DJANGO_DEBUG
    # ------------------------------------------------------------------
    if ! dotenv_has_key "$env_file" "DJANGO_DEBUG"; then
        if [ "$django_env" = "prod" ] && [ "$deploy_method" = "bare" ]; then
            # Production bare-metal: always off
            dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "0"
            info "DJANGO_DEBUG=0 (forced for production bare-metal)"
        else
            local default_debug="1"
            [ "$django_env" = "prod" ] && default_debug="0"
            read -p "DJANGO_DEBUG (1=on, 0=off, default: $default_debug): " -r DEBUG_INPUT
            DEBUG_INPUT="${DEBUG_INPUT:-$default_debug}"
            dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "$DEBUG_INPUT"
        fi
    fi

    # ------------------------------------------------------------------
    # DJANGO_ALLOWED_HOSTS
    # ------------------------------------------------------------------
    if ! dotenv_has_value "$env_file" "DJANGO_ALLOWED_HOSTS"; then
        if [ "$django_env" = "prod" ]; then
            local hosts
            hosts="$(prompt_non_empty "DJANGO_ALLOWED_HOSTS (comma-separated, e.g. example.com,www.example.com): ")"
            dotenv_set "$env_file" "DJANGO_ALLOWED_HOSTS" "$hosts"
        else
            read -p "DJANGO_ALLOWED_HOSTS (comma-separated, default: localhost,127.0.0.1): " -r ALLOWED_HOSTS_INPUT
            ALLOWED_HOSTS_INPUT="${ALLOWED_HOSTS_INPUT:-localhost,127.0.0.1}"
            dotenv_set "$env_file" "DJANGO_ALLOWED_HOSTS" "$ALLOWED_HOSTS_INPUT"
        fi
    fi

    # ------------------------------------------------------------------
    # DJANGO_SECRET_KEY
    # ------------------------------------------------------------------
    if ! dotenv_has_value "$env_file" "DJANGO_SECRET_KEY"; then
        local auto_prompt_default="Y/n"
        [ "$django_env" = "dev" ] && auto_prompt_default="y/N"

        read -p "Generate a secure DJANGO_SECRET_KEY now? [$auto_prompt_default]: " -n 1 -r
        echo ""
        local do_generate=false
        if [ "$django_env" = "prod" ]; then
            [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]] && do_generate=true
        else
            [[ "${REPLY:-}" =~ ^[Yy]$ ]] && do_generate=true
        fi

        if [ "$do_generate" = true ]; then
            if command_exists python3; then
                local key
                key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
                dotenv_set "$env_file" "DJANGO_SECRET_KEY" "$key"
                success "DJANGO_SECRET_KEY added to .env"
            else
                error "python3 not available; cannot generate DJANGO_SECRET_KEY."
                local manual_key
                manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
                dotenv_set "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
            fi
        elif [ "$django_env" = "prod" ]; then
            local manual_key
            manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
            dotenv_set "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
        else
            warn "DJANGO_SECRET_KEY not set. Set it manually before production use."
        fi
    fi

    # ------------------------------------------------------------------
    # Celery / Redis — skip broker prompt for Docker (Compose provides it)
    # ------------------------------------------------------------------
    if [ "$deploy_method" = "docker" ]; then
        # Docker Compose manages Redis internally
        dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"
        info "CELERY_BROKER_URL is managed by Docker Compose — skipping."
    else
        # bare — prompt for broker in prod, optional in dev
        if [ "$django_env" = "prod" ]; then
            if ! dotenv_has_value "$env_file" "CELERY_BROKER_URL"; then
                local broker
                broker="$(prompt_non_empty "CELERY_BROKER_URL (e.g. redis://redis:6379/0): ")"
                dotenv_set "$env_file" "CELERY_BROKER_URL" "$broker"
            fi

            if ! dotenv_has_value "$env_file" "CELERY_RESULT_BACKEND"; then
                read -p "Set CELERY_RESULT_BACKEND? [y/N]: " -n 1 -r
                echo ""
                if [[ "${REPLY:-}" =~ ^[Yy]$ ]]; then
                    local backend
                    backend="$(prompt_non_empty "CELERY_RESULT_BACKEND (e.g. redis://redis:6379/1): ")"
                    dotenv_set "$env_file" "CELERY_RESULT_BACKEND" "$backend"
                fi
            fi

            # Safety: never eager in prod
            dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"
        else
            # dev + bare: offer eager toggle
            if ! dotenv_has_key "$env_file" "CELERY_TASK_ALWAYS_EAGER"; then
                read -p "Run Celery tasks eagerly (no broker) for local dev? [y/N]: " -n 1 -r
                echo ""
                if [[ "${REPLY:-}" =~ ^[Yy]$ ]]; then
                    dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "1"
                else
                    dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"
                fi
            fi
        fi
    fi

    success ".env setup complete"
}

# ---------------------------------------------------------------------------
# Cluster role setup
# ---------------------------------------------------------------------------

dotenv_prompt_cluster() {
    local env_file="$PROJECT_DIR/.env"

    echo ""
    read -p "Configure this instance for multi-instance (cluster) mode? [y/N] " -n 1 -r
    echo ""
    if [[ ! "${REPLY:-}" =~ ^[Yy]$ ]]; then
        return 0
    fi

    echo ""
    echo "Select cluster role:"
    echo "  1) agent — run checkers locally, push results to a hub"
    echo "  2) hub   — accept alerts from remote agents"
    echo "  3) both  — agent + hub"
    echo ""
    read -p "Enter choice [1/2/3]: " -r CLUSTER_ROLE
    echo ""

    case "$CLUSTER_ROLE" in
        1|agent)  CLUSTER_ROLE="agent" ;;
        2|hub)    CLUSTER_ROLE="hub" ;;
        3|both)   CLUSTER_ROLE="both" ;;
        *)
            warn "Invalid choice '$CLUSTER_ROLE', skipping cluster setup."
            return 0
            ;;
    esac

    # Agent or both: prompt for HUB_URL and INSTANCE_ID
    if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        local hub_url
        hub_url="$(prompt_non_empty "HUB_URL (e.g. https://monitoring-hub.example.com): ")"
        dotenv_set "$env_file" "HUB_URL" "$hub_url"

        local default_id
        default_id="$(hostname 2>/dev/null || echo "")"
        read -p "INSTANCE_ID (default: $default_id): " -r INSTANCE_ID_INPUT
        INSTANCE_ID_INPUT="${INSTANCE_ID_INPUT:-$default_id}"
        if [ -n "$INSTANCE_ID_INPUT" ]; then
            dotenv_set "$env_file" "INSTANCE_ID" "$INSTANCE_ID_INPUT"
        fi
    fi

    # Hub or both: enable CLUSTER_ENABLED
    if [ "$CLUSTER_ROLE" = "hub" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        dotenv_set "$env_file" "CLUSTER_ENABLED" "1"
        success "CLUSTER_ENABLED=1 written to .env"
    fi

    # All roles: prompt for shared secret
    local secret
    secret="$(prompt_non_empty "WEBHOOK_SECRET_CLUSTER (shared secret between agents and hub): ")"
    dotenv_set "$env_file" "WEBHOOK_SECRET_CLUSTER" "$secret"

    success "Cluster configuration written to .env (role: $CLUSTER_ROLE)"

    # Agent or both: verify with dry-run
    if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        echo ""
        info "Running push_to_hub --dry-run to verify configuration..."
        if uv run python manage.py push_to_hub --dry-run 2>&1; then
            success "Dry run succeeded — agent is configured correctly"
        else
            warn "Dry run failed — check HUB_URL and try: uv run python manage.py push_to_hub --dry-run"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Main script body
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "   server-maintanence Installer"
echo "============================================"
echo ""

# --- Step 1: Select environment ---

echo "Select environment:"
echo "  1) dev   — development (DEBUG=1, eager tasks)"
echo "  2) prod  — production (DEBUG=0, real secret key)"
echo ""
read -p "Enter choice [1/2] (default: 1): " -r ENV_INPUT
ENV_INPUT="${ENV_INPUT:-1}"

case "$ENV_INPUT" in
    1|d|dev|Dev|DEV)   DJANGO_ENV="dev" ;;
    2|p|prod|Prod|PROD) DJANGO_ENV="prod" ;;
    *)
        warn "Invalid choice '$ENV_INPUT', defaulting to dev."
        DJANGO_ENV="dev"
        ;;
esac

info "Environment: $DJANGO_ENV"
echo ""

# --- Step 2: Select deployment method ---

echo "Select deployment method:"
echo "  1) bare   — bare-metal (Python + uv, systemd or runserver)"
echo "  2) docker — Docker Compose stack (requires Docker running)"
echo ""
read -p "Enter choice [1/2] (default: 1): " -r METHOD_INPUT
METHOD_INPUT="${METHOD_INPUT:-1}"

case "$METHOD_INPUT" in
    1|b|bare|Bare|BARE)     DEPLOY_METHOD="bare" ;;
    2|d|docker|Docker|DOCKER) DEPLOY_METHOD="docker" ;;
    *)
        warn "Invalid choice '$METHOD_INPUT', defaulting to bare."
        DEPLOY_METHOD="bare"
        ;;
esac

info "Deployment method: $DEPLOY_METHOD"

if [ "$DEPLOY_METHOD" = "docker" ]; then
    # -----------------------------------------------------------------------
    # Docker deployment
    # -----------------------------------------------------------------------
    info "Checking Docker daemon..."
    if ! command_exists docker || ! docker info >/dev/null 2>&1; then
        error "Docker is not running."
        echo "  Docker is required. Install it from https://docs.docker.com/get-docker/"
        echo "  and ensure the daemon is running."
        exit 1
    fi
    success "Docker daemon is running"

    dotenv_ensure_file
    dotenv_prompt_setup "$DJANGO_ENV" "$DEPLOY_METHOD"
    dotenv_prompt_cluster

    info "Handing off to deploy-docker.sh..."
    exec "$SCRIPT_DIR/deploy-docker.sh"
else
    # -----------------------------------------------------------------------
    # Bare-metal deployment (dev or prod)
    # -----------------------------------------------------------------------
    check_python || exit 1
    check_uv || exit 1

    # .env setup
    dotenv_ensure_file
    dotenv_prompt_setup "$DJANGO_ENV" "$DEPLOY_METHOD"

    # Sync dependencies
    if [ "$DJANGO_ENV" = "dev" ]; then
        info "Installing dependencies with uv sync (including development dependencies)..."
        uv sync --all-extras --dev
    else
        info "Installing dependencies with uv sync (production only)..."
        uv sync
    fi
    success "Dependencies installed"

    # Run migrations
    info "Running database migrations..."
    uv run python manage.py migrate
    success "Migrations applied"

    # Run Django system checks
    info "Running Django system checks..."
    if uv run python manage.py check; then
        success "All system checks passed"
    else
        warn "System checks reported issues (see above). You may want to address them."
    fi

    # Summary and next steps
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
    echo "  - Agent conventions:    agents.md"
    echo ""

    read -p "Would you like to run the health check suite now? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Running health checks..."
        uv run python manage.py check_health
    fi

    echo ""
    read -p "Would you like to set up automatic health checks via cron? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/setup_cron.sh"
    fi

    # Show existing pipeline definitions if any
    echo ""
    PIPELINE_COUNT=$(uv run python manage.py show_pipeline --json 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    if [ "$PIPELINE_COUNT" != "0" ] && [ "$PIPELINE_COUNT" != "" ]; then
        info "Found $PIPELINE_COUNT configured pipeline(s):"
        uv run python manage.py show_pipeline
        echo ""
    else
        info "No pipelines configured yet. Run the setup wizard to create one:"
        echo "  uv run python manage.py setup_instance"
        echo ""
    fi

    echo ""
    read -p "Would you like to set up shell aliases (e.g., sm-check-health)? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        "$SCRIPT_DIR/setup_aliases.sh"
    fi

    # Cluster role setup
    dotenv_prompt_cluster

    # Offer systemd deployment (prod + bare only)
    if [ "$DJANGO_ENV" = "prod" ] && [ "$DEPLOY_METHOD" = "bare" ]; then
        echo ""
        read -p "Would you like to deploy with systemd now? [y/N] " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            info "Handing off to deploy-systemd.sh..."
            echo "  Note: This requires root privileges."
            exec sudo "$SCRIPT_DIR/deploy-systemd.sh"
        fi
    fi

    success "Setup complete!"
fi
