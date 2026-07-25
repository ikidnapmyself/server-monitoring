#!/usr/bin/env bash
#
# Common prerequisite checks.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_CHECKS_LOADED:-}" ]] && return 0
_LIB_CHECKS_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
# paths.sh resolves UV_BIN (absolute uv path) and defines resolve_uv().
source "$_LIB_DIR/paths.sh"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for a working Python 3.10+ binary.
# Sets PYTHON_BIN on success, returns 1 on failure.
# Handles pyenv shims that exist but point to uninstalled versions.
check_python() {
    info "Checking Python version..."
    PYTHON_BIN=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command_exists "$candidate" && "$candidate" --version >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        error "Python 3 is not installed. Please install Python 3.10 or higher."
        return 1
    fi

    local version major minor
    version=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
        success "Python $version found via $PYTHON_BIN (>= 3.10 required)"
        return 0
    else
        error "Python 3.10+ is required, but found Python $version ($PYTHON_BIN)"
        return 1
    fi
}

# Check for uv package manager, install if missing.
# Returns 1 on failure.
check_uv() {
    info "Checking for uv package manager..."
    if resolve_uv && [ -n "$UV_BIN" ]; then
        local uv_version
        uv_version=$("$UV_BIN" --version 2>/dev/null | head -n1)
        success "uv is already installed: $uv_version ($UV_BIN)"
        return 0
    fi

    warn "uv is not installed. Installing uv..."

    if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh

        if [ -f "$HOME/.cargo/env" ]; then
            # shellcheck disable=SC1091
            source "$HOME/.cargo/env"
        fi

        # Re-resolve to the absolute path just installed — never rely on PATH.
        if resolve_uv && [ -n "$UV_BIN" ]; then
            success "uv installed successfully ($UV_BIN)"
            return 0
        else
            error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
            return 1
        fi
    else
        error "Unsupported OS. Please install uv manually: https://docs.astral.sh/uv/"
        return 1
    fi
}