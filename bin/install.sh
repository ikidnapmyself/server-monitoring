#!/bin/bash
#
# Installer for server-maintanence
#
# Usage:
#   install.sh              Run full installation (all steps)
#   install.sh <step>       Run a single step
#   install.sh help         Show available steps
#
# Steps:
#   env       Environment and core .env configuration
#   celery    Celery / Redis broker setup
#   cluster   Multi-instance cluster role configuration
#   deps      Install Python dependencies via uv
#   migrate   Run Django migrations and system checks
#   cron      Set up cron jobs for health checks
#   aliases   Set up shell aliases
#   deploy    Deploy via Docker Compose or systemd
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/paths.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"
source "$SCRIPT_DIR/lib/prompt.sh"
source "$SCRIPT_DIR/lib/profile.sh"

INSTALL_MOD_DIR="$SCRIPT_DIR/install"

cd "$PROJECT_DIR"

# ── Help ─────────────────────────────────────────────────────────────────────

show_usage() {
    echo ""
    echo "Usage: install.sh [step] [options]"
    echo ""
    echo "Options:"
    echo "  --profile FILE         Load saved profile (pre-fills prompts from FILE)"
    echo "  --yes, -y              Accept all defaults without prompting (secrets still prompted)"
    echo "  --save-profile [NAME]  Save configuration to profile after install"
    echo ""
    echo "Steps:"
    echo "  env       Environment and core .env configuration"
    echo "  celery    Celery / Redis broker setup"
    echo "  cluster   Multi-instance cluster role configuration"
    echo "  deps      Install Python dependencies via uv"
    echo "  migrate   Run Django migrations and system checks"
    echo "  cron      Set up cron jobs for health checks"
    echo "  aliases   Set up shell aliases (supports --remove, --list, --prefix)"
    echo "  deploy    Deploy via Docker Compose or systemd"
    echo ""
    echo "  help      Show this message"
    echo ""
    echo "Run with no arguments for the full guided installation."
    echo ""
}

# ── Full flow ────────────────────────────────────────────────────────────────

run_all() {
    echo ""
    echo "============================================"
    echo "   server-maintanence Installer"
    echo "============================================"
    echo ""

    source "$INSTALL_MOD_DIR/env.sh"
    source "$INSTALL_MOD_DIR/celery.sh"
    source "$INSTALL_MOD_DIR/cluster.sh"
    source "$INSTALL_MOD_DIR/deps.sh"
    source "$INSTALL_MOD_DIR/migrate.sh"
    source "$INSTALL_MOD_DIR/cron.sh"
    source "$INSTALL_MOD_DIR/aliases.sh"
    source "$INSTALL_MOD_DIR/deploy.sh"

    echo ""
    echo "============================================"
    printf "   %b Installation Complete! %b\n" "${GREEN:-}" "${NC:-}"
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
}

# ── Flag parsing ─────────────────────────────────────────────────────────────

INSTALL_PROFILE_FILE=""
INSTALL_SAVE_PROFILE=""
INSTALL_PROFILE_NAME=""
export INSTALL_AUTO_ACCEPT="${INSTALL_AUTO_ACCEPT:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            [[ $# -lt 2 ]] && { error "--profile requires a file argument"; exit 1; }
            INSTALL_PROFILE_FILE="$2"
            shift 2
            ;;
        --yes|-y)
            export INSTALL_AUTO_ACCEPT=1
            shift
            ;;
        --save-profile)
            INSTALL_SAVE_PROFILE=1
            if [[ $# -ge 2 && "${2:-}" != -* && "${2:-}" != "" ]]; then
                INSTALL_PROFILE_NAME="$2"
                shift
            fi
            shift
            ;;
        *)
            break
            ;;
    esac
done

# Resolve and load profile
if [[ -n "$INSTALL_PROFILE_FILE" ]]; then
    if [[ ! -f "$INSTALL_PROFILE_FILE" && -f "$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_FILE" ]]; then
        INSTALL_PROFILE_FILE="$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_FILE"
    fi
    profile_load "$INSTALL_PROFILE_FILE"
fi

# ── Dispatcher ───────────────────────────────────────────────────────────────

case "${1:-}" in
    env)      source "$INSTALL_MOD_DIR/env.sh"     ;;
    celery)   source "$INSTALL_MOD_DIR/celery.sh"  ;;
    cluster)  source "$INSTALL_MOD_DIR/cluster.sh" ;;
    deps)     source "$INSTALL_MOD_DIR/deps.sh"    ;;
    migrate)  source "$INSTALL_MOD_DIR/migrate.sh" ;;
    cron)     source "$INSTALL_MOD_DIR/cron.sh"    ;;
    aliases)
        shift
        source "$INSTALL_MOD_DIR/aliases.sh" "$@"
        ;;
    deploy)   source "$INSTALL_MOD_DIR/deploy.sh"  ;;
    help|-h|--help) show_usage                     ;;
    "")       run_all                              ;;
    *)        error "Unknown step: $1"; show_usage; exit 1 ;;
esac

# ── Post-install: save profile ───────────────────────────────────────────────

if [[ "${INSTALL_SAVE_PROFILE:-}" == "1" ]]; then
    _profile_path="$PROJECT_DIR/.install-profile"
    if [[ -n "${INSTALL_PROFILE_NAME:-}" ]]; then
        _profile_path="$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_NAME"
    fi
    profile_save "$_profile_path" "${INSTALL_PROFILE_NAME:-}"
fi