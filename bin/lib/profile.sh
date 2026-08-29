#!/usr/bin/env bash
#
# Install profile helpers — save/load installer configuration.
# Source this file — do not execute directly.
#
# Profiles store portable .env values and installer state variables
# (cron schedule, alias prefix, etc.) for reproducible installations. Secrets
# and machine-unique identity are deliberately left out.
#

[[ -n "${_LIB_PROFILE_LOADED:-}" ]] && return 0
_LIB_PROFILE_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"

# Secrets: withheld from profiles, and refused on load
PROFILE_SENSITIVE_KEYS=(DJANGO_SECRET_KEY HUB_API_KEY)

# Machine-unique — not secret, but not portable either. INSTANCE_ID keys this
# machine's Node row and its alert fingerprints, so copying one into a profile
# and restoring it elsewhere would silently merge two machines into one
# identity. A restoring machine generates its own (see profile_load).
PROFILE_MACHINE_KEYS=(INSTANCE_ID)

# Installer state variables not stored in .env
PROFILE_STATE_KEYS=(CRON_SCHEDULE CRON_AUTO_UPDATE CRON_PUSH_TO_HUB CRON_LOCAL_CHECK ALIAS_PREFIX)

PROFILE_VERSION=1

# profile_save FILE [NAME]
profile_save() {
    local file="$1"
    local name="${2:-}"
    local env_file="$PROJECT_DIR/.env"

    {
        echo "# server-maintanence install profile"
        echo "# name: ${name:-$(basename "$file")}"
        echo "# created: $(date -u +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || date +%Y-%m-%dT%H:%M:%S)"
        echo "# hostname: $(hostname 2>/dev/null || echo unknown)"
        echo "# installer_version: $PROFILE_VERSION"
        echo ""
    } > "$file"

    if [ -f "$env_file" ]; then
        while IFS= read -r line; do
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
            local key="${line%%=*}"
            key="${key#"${key%%[![:space:]]*}"}"
            local skip=false
            for sk in "${PROFILE_SENSITIVE_KEYS[@]}" "${PROFILE_MACHINE_KEYS[@]}"; do
                [[ "$key" == "$sk" ]] && { skip=true; break; }
            done
            $skip && continue
            echo "$line"
        done < "$env_file" >> "$file"
    fi

    local has_state=false
    for sk in "${PROFILE_STATE_KEYS[@]}"; do
        if [[ -n "${!sk:-}" ]]; then
            if [[ "$has_state" == false ]]; then
                echo "" >> "$file"
                echo "# Installer state" >> "$file"
                has_state=true
            fi
            printf "%s=%s\n" "$sk" "${!sk}" >> "$file"
        fi
    done

    success "Profile saved to $file"
}

# profile_load FILE
profile_load() {
    local file="$1"
    local env_file="$PROJECT_DIR/.env"

    if [[ ! -f "$file" ]]; then
        error "Profile not found: $file"
        return 1
    fi

    dotenv_ensure_file

    info "Loading profile: $file"
    local name
    name="$(profile_metadata "$file" "name")"
    [[ -n "$name" ]] && info "Profile name: $name"

    while IFS= read -r line; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        local key="${line%%=*}"
        local value="${line#*=}"

        for sk in "${PROFILE_SENSITIVE_KEYS[@]}"; do
            if [[ "$key" == "$sk" ]]; then
                warn "Skipping sensitive key '$key' from profile"
                continue 2
            fi
        done

        # An older or hand-edited profile may still carry a machine-unique key.
        for sk in "${PROFILE_MACHINE_KEYS[@]}"; do
            if [[ "$key" == "$sk" ]]; then
                info "Skipping machine-unique key '$key' — this machine keeps its own"
                continue 2
            fi
        done

        local is_state=false
        for sk in "${PROFILE_STATE_KEYS[@]}"; do
            if [[ "$key" == "$sk" ]]; then
                is_state=true
                export "$key=$value"
                break
            fi
        done

        if [[ "$is_state" == false ]]; then
            dotenv_set "$env_file" "$key" "$value"
        fi
    done < "$file"

    # A restore must not leave this machine without an identity: the installer
    # may run unattended (--yes), and the cluster step only prompts for
    # INSTANCE_ID when the operator opts into cluster configuration.
    dotenv_ensure_instance_id "$env_file"

    success "Profile loaded"
}

# profile_metadata FILE KEY
profile_metadata() {
    local file="$1"
    local key="$2"
    grep -E "^# ${key}:" "$file" 2>/dev/null \
        | head -1 | sed "s/^# ${key}:[[:space:]]*//"
}