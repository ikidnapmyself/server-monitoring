#!/usr/bin/env bash
#
# .env file helpers.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_DOTENV_LOADED:-}" ]] && return 0
_LIB_DOTENV_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/paths.sh"

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