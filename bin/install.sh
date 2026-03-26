#!/bin/bash
#
# Installer script for server-maintanence
# This script handles all setup steps according to README.md
#

set -e

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Change to project directory
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

dotenv_ensure_file() {
    local env_file="$PROJECT_DIR/.env"
    local sample_file="$PROJECT_DIR/.env.sample"

    if [ -f "$env_file" ]; then
        success ".env already exists"
        return 0
    fi

    if [ -f "$sample_file" ]; then
        cp "$sample_file" "$env_file"
        success "Created .env from .env.sample"
        return 0
    fi

    warn "No .env.sample found; creating empty .env"
    touch "$env_file"
}

dotenv_has_key() {
    local file="$1"
    local key="$2"
    grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$file"
}

dotenv_set_if_missing() {
    local file="$1"
    local key="$2"
    local value="$3"

    if dotenv_has_key "$file" "$key"; then
        return 0
    fi

    printf "%s=%s\n" "$key" "$value" >> "$file"
}

prompt_non_empty() {
    local prompt="$1"
    local value=""
    while true; do
        read -p "$prompt" -r value
        if [ -n "$value" ]; then
            echo "$value"
            return 0
        fi
        echo "Value cannot be empty."
    done
}

dotenv_prompt_setup() {
    local env_file="$PROJECT_DIR/.env"

    echo ""
    echo "============================================"
    echo "   Environment setup (.env)"
    echo "============================================"
    echo ""
    info "This will append missing values to .env (it will not overwrite existing entries)."

    local MODE=""
    while true; do
        read -p "Configure for [d]ev or [p]roduction? (default: dev): " -r MODE
        MODE="${MODE:-d}"
        case "$MODE" in
            d|dev|Dev|DEV) MODE="dev"; break ;;
            p|prod|production|Prod|PROD|PRODUCTION) MODE="prod"; break ;;
            *) warn "Please enter dev or production." ;;
        esac
    done

    # Expose selection outside this function.
    INSTALL_MODE="$MODE"

    # Set DJANGO_ENV based on mode (only if missing)
    if [ "$MODE" = "prod" ]; then
        dotenv_set_if_missing "$env_file" "DJANGO_ENV" "prod"
    else
        dotenv_set_if_missing "$env_file" "DJANGO_ENV" "dev"
    fi

    if [ "$MODE" = "prod" ]; then
        echo ""
        info "Production configuration"

        # DEBUG off in prod by default
        dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "0"

        # Required in prod: ALLOWED_HOSTS
        if ! dotenv_has_key "$env_file" "DJANGO_ALLOWED_HOSTS"; then
            local hosts
            hosts="$(prompt_non_empty "DJANGO_ALLOWED_HOSTS (comma-separated, e.g. example.com,www.example.com): ")"
            dotenv_set_if_missing "$env_file" "DJANGO_ALLOWED_HOSTS" "$hosts"
        fi

        # Required in prod: SECRET_KEY
        if ! dotenv_has_key "$env_file" "DJANGO_SECRET_KEY"; then
            read -p "Generate a secure DJANGO_SECRET_KEY now? [Y/n]: " -n 1 -r
            echo ""
            if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
                if command_exists python3; then
                    local key
                    key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
                    dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$key"
                    success "DJANGO_SECRET_KEY added to .env"
                else
                    error "python3 not available; cannot generate DJANGO_SECRET_KEY."
                    local manual_key
                    manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
                    dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
                fi
            else
                local manual_key
                manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
                dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
            fi
        fi

        # Celery / Redis: require broker URL in prod
        if ! dotenv_has_key "$env_file" "CELERY_BROKER_URL"; then
            local broker
            broker="$(prompt_non_empty "CELERY_BROKER_URL (e.g. redis://redis:6379/0): ")"
            dotenv_set_if_missing "$env_file" "CELERY_BROKER_URL" "$broker"
        fi

        # Results backend: optional, but common in prod
        if ! dotenv_has_key "$env_file" "CELERY_RESULT_BACKEND"; then
            read -p "Set CELERY_RESULT_BACKEND? [y/N]: " -n 1 -r
            echo ""
            if [[ "${REPLY:-}" =~ ^[Yy]$ ]]; then
                local backend
                backend="$(prompt_non_empty "CELERY_RESULT_BACKEND (e.g. redis://redis:6379/1): ")"
                dotenv_set_if_missing "$env_file" "CELERY_RESULT_BACKEND" "$backend"
            fi
        fi

        # Safety: never eager in prod
        dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"

    else
        echo ""
        info "Development configuration"

        # DJANGO_DEBUG
        if ! dotenv_has_key "$env_file" "DJANGO_DEBUG"; then
            read -p "Enable Django DEBUG? [Y/n]: " -n 1 -r
            echo ""
            if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
                dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "1"
            else
                dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "0"
            fi
        fi

        # DJANGO_ALLOWED_HOSTS
        if ! dotenv_has_key "$env_file" "DJANGO_ALLOWED_HOSTS"; then
            read -p "DJANGO_ALLOWED_HOSTS (comma-separated, default: localhost,127.0.0.1): " -r ALLOWED_HOSTS_INPUT
            ALLOWED_HOSTS_INPUT="${ALLOWED_HOSTS_INPUT:-localhost,127.0.0.1}"
            dotenv_set_if_missing "$env_file" "DJANGO_ALLOWED_HOSTS" "$ALLOWED_HOSTS_INPUT"
        fi

        # DJANGO_SECRET_KEY (dev: can generate or leave as-is)
        if ! dotenv_has_key "$env_file" "DJANGO_SECRET_KEY"; then
            read -p "Generate and set DJANGO_SECRET_KEY in .env? [y/N]: " -n 1 -r
            echo ""
            if [[ "${REPLY:-}" =~ ^[Yy]$ ]]; then
                if command_exists python3; then
                    DJANGO_SECRET_KEY_VALUE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
                    dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$DJANGO_SECRET_KEY_VALUE"
                    success "DJANGO_SECRET_KEY added to .env"
                else
                    warn "python3 not available; skipping DJANGO_SECRET_KEY generation"
                fi
            else
                warn "DJANGO_SECRET_KEY not set. Set it manually before production use."
            fi
        fi

        # Celery eager toggle (useful for local)
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

    success ".env setup complete"
}

dotenv_prompt_docker() {
    local env_file="$PROJECT_DIR/.env"

    echo ""
    echo "============================================"
    echo "   Docker environment setup (.env)"
    echo "============================================"
    echo ""
    info "Configuring .env for Docker Compose deployment."
    info "CELERY_BROKER_URL is managed by Docker Compose — skipping."
    echo ""

    # DJANGO_DEBUG
    if ! dotenv_has_key "$env_file" "DJANGO_DEBUG"; then
        read -p "DJANGO_DEBUG (1=on, 0=off, default: 1): " -r DEBUG_INPUT
        DEBUG_INPUT="${DEBUG_INPUT:-1}"
        dotenv_set_if_missing "$env_file" "DJANGO_DEBUG" "$DEBUG_INPUT"
    fi

    # DJANGO_SECRET_KEY
    if ! dotenv_has_key "$env_file" "DJANGO_SECRET_KEY"; then
        read -p "Generate a secure DJANGO_SECRET_KEY now? [Y/n]: " -n 1 -r
        echo ""
        if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
            if command_exists python3; then
                local key
                key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
                dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$key"
                success "DJANGO_SECRET_KEY added to .env"
            else
                error "python3 not available; cannot generate DJANGO_SECRET_KEY."
                local manual_key
                manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
                dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
            fi
        else
            local manual_key
            manual_key="$(prompt_non_empty "Paste DJANGO_SECRET_KEY to store in .env: ")"
            dotenv_set_if_missing "$env_file" "DJANGO_SECRET_KEY" "$manual_key"
        fi
    fi

    # DJANGO_ALLOWED_HOSTS
    if ! dotenv_has_key "$env_file" "DJANGO_ALLOWED_HOSTS"; then
        read -p "DJANGO_ALLOWED_HOSTS (comma-separated, default: localhost,127.0.0.1): " -r HOSTS_INPUT
        HOSTS_INPUT="${HOSTS_INPUT:-localhost,127.0.0.1}"
        dotenv_set_if_missing "$env_file" "DJANGO_ALLOWED_HOSTS" "$HOSTS_INPUT"
    fi

    # Safety: never eager in docker (Compose provides Redis)
    dotenv_set_if_missing "$env_file" "CELERY_TASK_ALWAYS_EAGER" "0"

    success ".env setup complete (Docker mode)"
}

# ---------------------------------------------------------------------------
# Helper functions extracted from main body
# ---------------------------------------------------------------------------

check_python() {
    info "Checking Python version..."
    PYTHON_BIN=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command_exists "$candidate"; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        error "Python 3 is not installed. Please install Python 3.10 or higher."
        exit 1
    fi

    PYTHON_VERSION=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        success "Python $PYTHON_VERSION found via $PYTHON_BIN (>= 3.10 required)"
    else
        error "Python 3.10+ is required, but found Python $PYTHON_VERSION ($PYTHON_BIN)"
        info "Tip: If Python 3.10+ is installed under a different name, set PYTHON_BIN and re-run."
        exit 1
    fi
}

check_uv() {
    info "Checking for uv package manager..."
    if command_exists uv; then
        UV_VERSION=$(uv --version 2>/dev/null | head -n1)
        success "uv is already installed: $UV_VERSION"
    else
        warn "uv is not installed. Installing uv..."

        if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
            curl -LsSf https://astral.sh/uv/install.sh | sh

            if [ -f "$HOME/.cargo/env" ]; then
                source "$HOME/.cargo/env"
            fi

            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

            if command_exists uv; then
                success "uv installed successfully"
            else
                error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
                exit 1
            fi
        else
            error "Unsupported OS. Please install uv manually: https://docs.astral.sh/uv/"
            exit 1
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

# Mode selection
echo "Select installation mode:"
echo "  1) dev    — local development (DEBUG=1, eager tasks)"
echo "  2) prod   — bare-metal production (systemd, gunicorn)"
echo "  3) docker — Docker Compose stack (requires Docker running)"
echo ""
read -p "Enter choice [1/2/3] (default: 1): " -r MODE_INPUT
MODE_INPUT="${MODE_INPUT:-1}"

case "$MODE_INPUT" in
    1|d|dev|Dev|DEV)       INSTALL_MODE="dev" ;;
    2|p|prod|Prod|PROD)    INSTALL_MODE="prod" ;;
    3|docker|Docker|DOCKER) INSTALL_MODE="docker" ;;
    *)
        warn "Invalid choice '$MODE_INPUT', defaulting to dev."
        INSTALL_MODE="dev"
        ;;
esac

info "Selected mode: $INSTALL_MODE"

if [ "$INSTALL_MODE" = "docker" ]; then
    # -----------------------------------------------------------------------
    # Docker mode
    # -----------------------------------------------------------------------
    info "Checking Docker daemon..."
    if ! command_exists docker; then
        error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        error "Docker daemon is not running. Please start Docker and try again."
        exit 1
    fi
    success "Docker daemon is running"

    dotenv_ensure_file
    dotenv_prompt_docker

    info "Handing off to deploy-docker.sh..."
    exec "$SCRIPT_DIR/deploy-docker.sh"
else
    # -----------------------------------------------------------------------
    # Dev / Prod mode
    # -----------------------------------------------------------------------
    check_python
    check_uv

    # .env setup
    dotenv_ensure_file
    dotenv_prompt_setup

    # Sync dependencies
    if [ "$INSTALL_MODE" = "dev" ]; then
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

    success "Setup complete!"
fi
