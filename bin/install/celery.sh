#!/usr/bin/env bash
#
# Installer module: Celery / Redis broker configuration.
#
# Configures: CELERY_BROKER_URL, CELERY_RESULT_BACKEND, CELERY_TASK_ALWAYS_EAGER
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
echo "   Celery / Redis Broker Setup"
echo "============================================"
echo ""

# ---------------------------------------------------------------------------
# Read current environment settings
# ---------------------------------------------------------------------------

DJANGO_ENV="$(dotenv_get "$_ENV_FILE" "DJANGO_ENV")"
DEPLOY_METHOD="$(dotenv_get "$_ENV_FILE" "DEPLOY_METHOD")"

# ---------------------------------------------------------------------------
# Docker mode — broker is managed by Compose, just set eager=0
# ---------------------------------------------------------------------------

if [ "$DEPLOY_METHOD" = "docker" ]; then
    dotenv_set "$_ENV_FILE" "CELERY_TASK_ALWAYS_EAGER" "0"
    info "CELERY_BROKER_URL is managed by Docker Compose — skipping broker prompts."
    success "Celery configuration complete (Docker mode)."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Bare-metal + production
# ---------------------------------------------------------------------------

if [ "$DJANGO_ENV" = "prod" ]; then
    # Broker URL is required in production
    CELERY_BROKER_URL=$(prompt_with_default "$_ENV_FILE" "CELERY_BROKER_URL" \
        "CELERY_BROKER_URL (e.g. redis://redis:6379/0)")

    dotenv_set "$_ENV_FILE" "CELERY_BROKER_URL" "$CELERY_BROKER_URL"

    # Optionally configure result backend
    if prompt_yes_no "Configure CELERY_RESULT_BACKEND?"; then
        CELERY_RESULT_BACKEND=$(prompt_with_default "$_ENV_FILE" "CELERY_RESULT_BACKEND" \
            "CELERY_RESULT_BACKEND (e.g. redis://redis:6379/1)")

        dotenv_set "$_ENV_FILE" "CELERY_RESULT_BACKEND" "$CELERY_RESULT_BACKEND"
    fi

    # Never eager in production
    dotenv_set "$_ENV_FILE" "CELERY_TASK_ALWAYS_EAGER" "0"
    info "CELERY_TASK_ALWAYS_EAGER=0 (forced for production)"

    success "Celery configuration complete (production)."
    return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# Bare-metal + development
# ---------------------------------------------------------------------------

EAGER_MODE=$(prompt_choice "$_ENV_FILE" "CELERY_TASK_ALWAYS_EAGER" \
    "Celery task execution mode:" \
    "1:eager — run tasks in-process (no broker needed)" \
    "0:worker — use a real broker (Redis, RabbitMQ, etc.)")

dotenv_set "$_ENV_FILE" "CELERY_TASK_ALWAYS_EAGER" "$EAGER_MODE"

if [ "$EAGER_MODE" = "0" ]; then
    CELERY_BROKER_URL=$(prompt_with_default "$_ENV_FILE" "CELERY_BROKER_URL" \
        "CELERY_BROKER_URL" "redis://localhost:6379/0")

    dotenv_set "$_ENV_FILE" "CELERY_BROKER_URL" "$CELERY_BROKER_URL"
fi

success "Celery configuration complete (development)."

return 0 2>/dev/null || exit 0