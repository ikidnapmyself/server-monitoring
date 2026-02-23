#!/usr/bin/env bash
#
# Setup shell aliases for Server Maintenance management commands.
#
# Usage:
#   ./bin/setup_aliases.sh                  # Interactive setup (default prefix: sm)
#   ./bin/setup_aliases.sh --prefix maint   # Custom prefix
#   ./bin/setup_aliases.sh --remove         # Remove aliases and source line
#   ./bin/setup_aliases.sh --list           # Show current aliases
#   ./bin/setup_aliases.sh --help           # Show help
#

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ALIASES_FILE="$SCRIPT_DIR/aliases.sh"
MARKER="# server-maintanence aliases"

# ── Helpers ──────────────────────────────────────────────────────────────────
info()    { printf "${BLUE}[info]${NC}  %s\n" "$*"; }
success() { printf "${GREEN}[ok]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${NC}  %s\n" "$*"; }
error()   { printf "${RED}[error]${NC} %s\n" "$*" >&2; }

detect_profile() {
    local shell_name
    shell_name="$(basename "$SHELL")"
    case "$shell_name" in
        zsh)  echo "$HOME/.zshrc" ;;
        bash) echo "$HOME/.bashrc" ;;
        *)
            warn "Unknown shell '$shell_name', falling back to ~/.profile"
            echo "$HOME/.profile"
            ;;
    esac
}

# ── show_help ────────────────────────────────────────────────────────────────
show_help() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Set up shell aliases for server-maintanence management commands.

Options:
  --prefix VALUE   Alias prefix (default: sm)
                   Example: --prefix maint  =>  maint-check-health, ...
  --remove         Remove generated aliases and the source line from shell profile
  --list           Show currently generated aliases
  --help           Show this help message

Examples:
  $(basename "$0")                  # Interactive setup (prompts for prefix)
  $(basename "$0") --prefix sm      # Generate aliases with 'sm' prefix
  $(basename "$0") --remove         # Clean up everything
  $(basename "$0") --list           # Display current aliases
EOF
}

# ── show_list ────────────────────────────────────────────────────────────────
show_list() {
    if [[ ! -f "$ALIASES_FILE" ]]; then
        warn "No aliases file found at $ALIASES_FILE"
        info "Run '$(basename "$0")' to generate aliases."
        return 1
    fi
    info "Current aliases from $ALIASES_FILE:"
    echo ""
    grep '^alias ' "$ALIASES_FILE" | sed 's/^alias /  /' | sed "s/=/ => /"
    echo ""
}

# ── generate_aliases ─────────────────────────────────────────────────────────
generate_aliases() {
    local prefix="$1"

    cat > "$ALIASES_FILE" <<ALIASES
#!/usr/bin/env bash
#
# Server Maintenance shell aliases (auto-generated)
# Prefix: ${prefix}
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
#
# To re-generate:  ${SCRIPT_DIR}/setup_aliases.sh --prefix ${prefix}
# To remove:       ${SCRIPT_DIR}/setup_aliases.sh --remove
#

alias ${prefix}-check-health='cd "${PROJECT_DIR}" && uv run python manage.py check_health'
alias ${prefix}-run-check='cd "${PROJECT_DIR}" && uv run python manage.py run_check'
alias ${prefix}-check-and-alert='cd "${PROJECT_DIR}" && uv run python manage.py check_and_alert'
alias ${prefix}-get-recommendations='cd "${PROJECT_DIR}" && uv run python manage.py get_recommendations'
alias ${prefix}-run-pipeline='cd "${PROJECT_DIR}" && uv run python manage.py run_pipeline'
alias ${prefix}-monitor-pipeline='cd "${PROJECT_DIR}" && uv run python manage.py monitor_pipeline'
alias ${prefix}-test-notify='cd "${PROJECT_DIR}" && uv run python manage.py test_notify'
alias ${prefix}-list-notify-drivers='cd "${PROJECT_DIR}" && uv run python manage.py list_notify_drivers'
alias ${prefix}-setup-instance='cd "${PROJECT_DIR}" && uv run python manage.py setup_instance'
alias ${prefix}-cli='${SCRIPT_DIR}/cli.sh'
ALIASES

    success "Generated $ALIASES_FILE with prefix '${prefix}'"
}

# ── install_source_line ──────────────────────────────────────────────────────
install_source_line() {
    local profile
    profile="$(detect_profile)"

    if [[ ! -f "$profile" ]]; then
        warn "Profile '$profile' does not exist; creating it."
        touch "$profile"
    fi

    local source_line="source \"${ALIASES_FILE}\"  ${MARKER}"

    if grep -qF "$MARKER" "$profile" 2>/dev/null; then
        # Replace existing line (path may have changed)
        local tmp
        tmp="$(mktemp)"
        grep -vF "$MARKER" "$profile" > "$tmp"
        echo "$source_line" >> "$tmp"
        mv "$tmp" "$profile"
        info "Updated source line in $profile"
    else
        echo "$source_line" >> "$profile"
        info "Added source line to $profile"
    fi

    success "Aliases installed. Restart your shell or run:"
    echo ""
    echo "  source \"$profile\""
    echo ""
}

# ── do_remove ────────────────────────────────────────────────────────────────
do_remove() {
    local profile
    profile="$(detect_profile)"
    local removed_something=false

    # Remove aliases file
    if [[ -f "$ALIASES_FILE" ]]; then
        rm "$ALIASES_FILE"
        success "Removed $ALIASES_FILE"
        removed_something=true
    else
        info "No aliases file to remove."
    fi

    # Remove source line from profile
    if [[ -f "$profile" ]] && grep -qF "$MARKER" "$profile" 2>/dev/null; then
        local tmp
        tmp="$(mktemp)"
        grep -vF "$MARKER" "$profile" > "$tmp"
        mv "$tmp" "$profile"
        success "Removed source line from $profile"
        removed_something=true
    else
        info "No source line found in $profile."
    fi

    if [[ "$removed_something" == true ]]; then
        success "Cleanup complete. Restart your shell to apply."
    else
        info "Nothing to remove."
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    local prefix=""
    local action="setup"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --prefix)
                if [[ $# -lt 2 ]]; then
                    error "--prefix requires a value"
                    exit 1
                fi
                prefix="$2"
                shift 2
                ;;
            --remove)
                action="remove"
                shift
                ;;
            --list)
                action="list"
                shift
                ;;
            --help|-h)
                action="help"
                shift
                ;;
            *)
                error "Unknown option: $1"
                echo ""
                show_help
                exit 1
                ;;
        esac
    done

    case "$action" in
        help)
            show_help
            return 0
            ;;
        list)
            show_list
            return $?
            ;;
        remove)
            do_remove
            return 0
            ;;
        setup)
            # If no prefix and stdin is a terminal, prompt interactively
            if [[ -z "$prefix" ]]; then
                if [[ -t 0 ]]; then
                    printf "${BLUE}Enter alias prefix${NC} [sm]: "
                    read -r prefix
                    prefix="${prefix:-sm}"
                else
                    prefix="sm"
                fi
            fi

            # Validate prefix (alphanumeric, hyphens, underscores)
            if [[ ! "$prefix" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
                error "Invalid prefix '$prefix'. Must start with a letter and contain only letters, digits, hyphens, or underscores."
                exit 1
            fi

            info "Using prefix: $prefix"
            generate_aliases "$prefix"
            install_source_line
            ;;
    esac
}

main "$@"
