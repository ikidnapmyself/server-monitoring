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

dotenv_has_value() {
    local file="$1"
    local key="$2"
    grep -Eq "^[[:space:]]*${key}[[:space:]]*=.+" "$file"
}

dotenv_get() {
    local file="$1"
    local key="$2"
    grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null \
        | tail -1 | sed "s/^[[:space:]]*${key}[[:space:]]*=//"
}

dotenv_set() {
    local file="$1"
    local key="$2"
    local value="$3"

    if dotenv_has_key "$file" "$key"; then
        # Replace existing line (handles empty and non-empty values)
        sed -i'' -e "s|^[[:space:]]*${key}[[:space:]]*=.*|${key}=${value}|" "$file"
    else
        printf "%s=%s\n" "$key" "$value" >> "$file"
    fi
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

# dotenv_default_instance_id
#
# Generate this machine's INSTANCE_ID: hostname plus a short random suffix.
# INSTANCE_ID keys the machine's Node row and its check:{instance_id}:{checker}
# alert fingerprints, so a bare hostname is not enough — two stock machines
# report the same one and would collapse into a single identity.
dotenv_default_instance_id() {
    printf '%s-%s\n' \
        "$(hostname 2>/dev/null || echo "node")" \
        "$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
}

# dotenv_ensure_instance_id FILE
#
# Guarantee FILE has a non-empty INSTANCE_ID, generating one only when it does
# not. Every install needs an identity, not just a clustered one: a standalone
# machine still registers a Node row and fingerprints its own alerts, and
# without an id it falls back to the bare hostname the design rejects.
#
# Idempotent, and deliberately so: an id already in .env is this machine's
# identity, and regenerating it would orphan its Node row and stop every open
# incident's alerts from matching. Never overwrite.
dotenv_ensure_instance_id() {
    local file="$1"

    if dotenv_has_value "$file" "INSTANCE_ID"; then
        return 0
    fi

    local generated
    generated="$(dotenv_default_instance_id)"
    dotenv_set "$file" "INSTANCE_ID" "$generated"
    info "INSTANCE_ID for this machine: $generated"
    info "It keys this machine's identity — keep it stable once set."
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