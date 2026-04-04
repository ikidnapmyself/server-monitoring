#!/usr/bin/env bash
#
# Installer module: shell aliases for server-maintanence management commands.
#
# When sourced from install.sh, operates in interactive mode by default.
# Supports sub-flags via positional parameters:
#
#   --remove          Remove aliases file + source line from profile
#   --list            Show current aliases
#   --help            Show usage
#   --prefix VALUE    Use VALUE as alias prefix (skip prompt)
#   (no flag)         Interactive mode — prompt for prefix
#
# Source this file from install.sh, or run directly for standalone use.
#

# ---------------------------------------------------------------------------
# Bootstrap paths and dependencies
# ---------------------------------------------------------------------------

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/paths.sh"
source "$_LIB_DIR/prompt.sh"

ALIASES_FILE="$BIN_DIR/aliases.sh"
_ALIASES_MARKER="# server-maintanence aliases"

# ---------------------------------------------------------------------------
# detect_profile — find the user's shell profile
# ---------------------------------------------------------------------------

_aliases_detect_profile() {
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

# ---------------------------------------------------------------------------
# show_help
# ---------------------------------------------------------------------------

_aliases_show_help() {
    cat <<EOF
Usage: install.sh aliases [OPTIONS]

Set up shell aliases for server-maintanence management commands.

Options:
  --prefix VALUE   Alias prefix (default: sm)
                   Example: --prefix maint  =>  maint-check-health, ...
  --remove         Remove generated aliases and the source line from shell profile
  --list           Show currently generated aliases
  --help           Show this help message

Examples:
  install.sh aliases                  # Interactive setup (prompts for prefix)
  install.sh aliases --prefix sm      # Generate aliases with 'sm' prefix
  install.sh aliases --remove         # Clean up everything
  install.sh aliases --list           # Display current aliases
EOF
}

# ---------------------------------------------------------------------------
# show_list — display current aliases
# ---------------------------------------------------------------------------

_aliases_show_list() {
    if [[ ! -f "$ALIASES_FILE" ]]; then
        warn "No aliases file found at $ALIASES_FILE"
        info "Run 'install.sh aliases' to generate aliases."
        return 1
    fi
    info "Current aliases from $ALIASES_FILE:"
    echo ""
    grep '^alias ' "$ALIASES_FILE" | sed 's/^alias /  /' | sed "s/=/ => /"
    echo ""
}

# ---------------------------------------------------------------------------
# generate_aliases — write the aliases file
# ---------------------------------------------------------------------------

_aliases_generate() {
    local prefix="$1"

    cat > "$ALIASES_FILE" <<ALIASES
#!/usr/bin/env bash
#
# Server Maintenance shell aliases (auto-generated)
# Prefix: ${prefix}
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
#
# To re-generate:  ${BIN_DIR}/install.sh aliases --prefix ${prefix}
# To remove:       ${BIN_DIR}/install.sh aliases --remove
#

alias ${prefix}-check-health='cd "${PROJECT_DIR}" && uv run python manage.py check_health'
alias ${prefix}-run-check='cd "${PROJECT_DIR}" && uv run python manage.py run_check'
alias ${prefix}-check-and-alert='cd "${PROJECT_DIR}" && uv run python manage.py run_pipeline --checks-only'
alias ${prefix}-get-recommendations='cd "${PROJECT_DIR}" && uv run python manage.py get_recommendations'
alias ${prefix}-run-pipeline='cd "${PROJECT_DIR}" && uv run python manage.py run_pipeline'
alias ${prefix}-monitor-pipeline='cd "${PROJECT_DIR}" && uv run python manage.py monitor_pipeline'
alias ${prefix}-test-notify='cd "${PROJECT_DIR}" && uv run python manage.py test_notify'
alias ${prefix}-setup-instance='cd "${PROJECT_DIR}" && uv run python manage.py setup_instance'
alias ${prefix}-cli='${BIN_DIR}/cli.sh'
ALIASES

    success "Generated $ALIASES_FILE with prefix '${prefix}'"
}

# ---------------------------------------------------------------------------
# install_source_line — add source line to shell profile
# ---------------------------------------------------------------------------

_aliases_install_source_line() {
    local profile
    profile="$(_aliases_detect_profile)"

    if [[ ! -f "$profile" ]]; then
        warn "Profile '$profile' does not exist; creating it."
        touch "$profile"
    fi

    local source_line="source \"${ALIASES_FILE}\"  ${_ALIASES_MARKER}"

    if grep -qF "$_ALIASES_MARKER" "$profile" 2>/dev/null; then
        # Replace existing line (path may have changed)
        local tmp
        tmp="$(mktemp)"
        grep -vF "$_ALIASES_MARKER" "$profile" > "$tmp"
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

# ---------------------------------------------------------------------------
# do_remove — remove aliases file and source line from profile
# ---------------------------------------------------------------------------

_aliases_do_remove() {
    local profile
    profile="$(_aliases_detect_profile)"
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
    if [[ -f "$profile" ]] && grep -qF "$_ALIASES_MARKER" "$profile" 2>/dev/null; then
        local tmp
        tmp="$(mktemp)"
        grep -vF "$_ALIASES_MARKER" "$profile" > "$tmp"
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

# ---------------------------------------------------------------------------
# Read existing prefix from current aliases file (if any)
# ---------------------------------------------------------------------------

_aliases_read_existing_prefix() {
    if [[ -f "$ALIASES_FILE" ]]; then
        local line
        line="$(grep '^# Prefix:' "$ALIASES_FILE" 2>/dev/null || true)"
        if [[ -n "$line" ]]; then
            echo "${line#*: }"
            return 0
        fi
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

_aliases_main() {
    local prefix=""
    local action="setup"

    local -a args=("$@")

    # Parse arguments
    local i=0
    while [[ $i -lt ${#args[@]} ]]; do
        case "${args[$i]}" in
            --prefix)
                if [[ $((i + 1)) -ge ${#args[@]} ]]; then
                    error "--prefix requires a value"
                    return 1
                fi
                prefix="${args[$((i + 1))]}"
                i=$((i + 2))
                ;;
            --remove)
                action="remove"
                i=$((i + 1))
                ;;
            --list)
                action="list"
                i=$((i + 1))
                ;;
            --help|-h)
                action="help"
                i=$((i + 1))
                ;;
            *)
                error "Unknown option: ${args[$i]}"
                echo ""
                _aliases_show_help
                return 1
                ;;
        esac
    done

    case "$action" in
        help)
            _aliases_show_help
            return 0
            ;;
        list)
            _aliases_show_list
            return $?
            ;;
        remove)
            _aliases_do_remove
            return 0
            ;;
        setup)
            echo ""
            echo "============================================"
            echo "   Shell Aliases Setup"
            echo "============================================"
            echo ""

            # If no prefix given, prompt interactively
            if [[ -z "$prefix" ]]; then
                local existing
                existing="$(_aliases_read_existing_prefix)"
                local fallback="${existing:-sm}"

                if [[ -t 0 ]]; then
                    printf "Alias prefix [%s]: " "$fallback" >&2
                    read -r prefix
                    prefix="${prefix:-$fallback}"
                else
                    prefix="$fallback"
                fi
            fi

            # Validate prefix (must start with letter, then letters/digits/hyphens/underscores)
            if [[ ! "$prefix" =~ ^[a-zA-Z][a-zA-Z0-9_-]*$ ]]; then
                error "Invalid prefix '$prefix'. Must start with a letter and contain only letters, digits, hyphens, or underscores."
                return 1
            fi

            info "Using prefix: $prefix"
            _aliases_generate "$prefix"
            _aliases_install_source_line
            ;;
    esac
}

_aliases_main "$@"

return 0 2>/dev/null || exit 0