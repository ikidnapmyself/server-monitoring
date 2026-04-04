#!/bin/bash
#
# Convert a dev environment to production.
# Sets DJANGO_ENV=prod, DJANGO_DEBUG=0, ensures secret key and allowed hosts.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/checks.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"

cd "$PROJECT_DIR"

# --help
for arg in "$@"; do
    case $arg in
        --help|-h)
            echo "Usage: bin/set_production.sh"
            echo ""
            echo "Convert a dev environment to production."
            echo ""
            echo "What it does:"
            echo "  1. Set DJANGO_ENV=prod in .env"
            echo "  2. Set DJANGO_DEBUG=0 in .env"
            echo "  3. Ensure DJANGO_SECRET_KEY has a value"
            echo "  4. Ensure DJANGO_ALLOWED_HOSTS has a value"
            echo "  5. Re-sync dependencies (without dev extras)"
            echo ""
            echo "Safe to run multiple times (idempotent)."
            exit 0
            ;;
    esac
done

ENV_FILE="$PROJECT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    error ".env not found. Run bin/install.sh first."
    exit 1
fi

echo ""
echo "============================================"
echo "   Convert to Production"
echo "============================================"
echo ""

CHANGES=()

# 1. DJANGO_ENV=prod
dotenv_set "$ENV_FILE" "DJANGO_ENV" "prod"
CHANGES+=("DJANGO_ENV=prod")

# 2. DJANGO_DEBUG=0
dotenv_set "$ENV_FILE" "DJANGO_DEBUG" "0"
CHANGES+=("DJANGO_DEBUG=0")

# 3. DJANGO_SECRET_KEY
if ! dotenv_has_value "$ENV_FILE" "DJANGO_SECRET_KEY"; then
    info "DJANGO_SECRET_KEY is empty — generating a secure key..."
    if command_exists python3; then
        KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
        dotenv_set "$ENV_FILE" "DJANGO_SECRET_KEY" "$KEY"
        CHANGES+=("DJANGO_SECRET_KEY=<generated>")
        success "DJANGO_SECRET_KEY generated and written to .env"
    else
        error "python3 not available; cannot generate DJANGO_SECRET_KEY."
        KEY="$(prompt_non_empty "Paste DJANGO_SECRET_KEY: ")"
        dotenv_set "$ENV_FILE" "DJANGO_SECRET_KEY" "$KEY"
        CHANGES+=("DJANGO_SECRET_KEY=<manual>")
    fi
else
    info "DJANGO_SECRET_KEY already set"
fi

# 4. DJANGO_ALLOWED_HOSTS
if ! dotenv_has_value "$ENV_FILE" "DJANGO_ALLOWED_HOSTS"; then
    HOSTS="$(prompt_non_empty "DJANGO_ALLOWED_HOSTS (comma-separated, e.g. example.com,www.example.com): ")"
    dotenv_set "$ENV_FILE" "DJANGO_ALLOWED_HOSTS" "$HOSTS"
    CHANGES+=("DJANGO_ALLOWED_HOSTS=$HOSTS")
else
    info "DJANGO_ALLOWED_HOSTS already set"
fi

# 5. Re-sync dependencies (drop dev extras)
echo ""
info "Re-syncing dependencies (production only)..."
if command_exists uv; then
    uv sync
    success "Dependencies synced (dev extras removed)"
else
    warn "uv not found — skipping dependency sync"
fi

# Summary
echo ""
echo "============================================"
echo -e "${GREEN}   Production conversion complete${NC}"
echo "============================================"
echo ""
info "Changes applied:"
for change in "${CHANGES[@]}"; do
    echo "  - $change"
done
echo ""
info "Next steps:"
echo "  - Run health check:  uv run python manage.py check_health"
echo "  - Run system check:  bin/check_system.sh"
echo "  - Deploy systemd:    sudo bin/install.sh deploy"
echo ""