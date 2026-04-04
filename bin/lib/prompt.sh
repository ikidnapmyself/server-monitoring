#!/usr/bin/env bash
#
# Interactive prompt helpers for installer modules.
# Source this file — do not execute directly.
#
# All display output goes to stderr so stdout remains clean for value capture.
#

[[ -n "${_LIB_PROMPT_LOADED:-}" ]] && return 0
_LIB_PROMPT_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/dotenv.sh"

# prompt_with_default ENV_FILE KEY "label" [fallback]
#
# Read KEY from ENV_FILE. Show current value (or fallback) as default.
# User presses Enter to keep existing, or types new value.
# Prints final value to stdout.
# If PROMPT_MASK=1, displays "••••••••" instead of actual value.
# If no existing value, no fallback, and empty input: loop until non-empty.
prompt_with_default() {
    local env_file="$1"
    local key="$2"
    local label="$3"
    local fallback="${4:-}"

    local current=""
    if [ -f "$env_file" ] && dotenv_has_value "$env_file" "$key"; then
        current="$(dotenv_get "$env_file" "$key")"
    fi

    local default="${current:-$fallback}"

    # Auto-accept: return default without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]] && [[ -n "$default" ]]; then
        printf '%s\n' "$default"
        return 0
    fi

    local display_default="$default"

    if [[ "${PROMPT_MASK:-0}" == "1" ]] && [[ -n "$default" ]]; then
        display_default="••••••••"
    fi

    local value=""
    while true; do
        if [[ -n "$default" ]]; then
            printf "%s [%s]: " "$label" "$display_default" >&2
        else
            printf "%s: " "$label" >&2
        fi

        read -r value

        # Empty input: use default if available
        if [[ -z "$value" ]]; then
            if [[ -n "$default" ]]; then
                value="$default"
                break
            fi
            warn "Value cannot be empty." >&2
            continue
        fi

        break
    done

    printf '%s\n' "$value"
}

# prompt_choice ENV_FILE KEY "label" "opt1:desc1" "opt2:desc2" ...
#
# Show numbered menu. Read current KEY from ENV_FILE as default.
# Accept numeric input or text matching option value.
# Print selected value to stdout.
prompt_choice() {
    local env_file="$1"
    local key="$2"
    local label="$3"
    shift 3

    local -a options=("$@")
    local current=""
    if [ -f "$env_file" ] && dotenv_has_value "$env_file" "$key"; then
        current="$(dotenv_get "$env_file" "$key")"
    fi

    # Auto-accept: return current value without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]] && [[ -n "$current" ]]; then
        printf '%s\n' "$current"
        return 0
    fi

    # Display menu header
    printf "\n%s\n" "$label" >&2

    local i=1
    local opt_val opt_desc
    for opt in "${options[@]}"; do
        opt_val="${opt%%:*}"
        opt_desc="${opt#*:}"
        if [[ "$opt_val" == "$current" ]]; then
            printf "  %d) %s — %s (current)\n" "$i" "$opt_val" "$opt_desc" >&2
        else
            printf "  %d) %s — %s\n" "$i" "$opt_val" "$opt_desc" >&2
        fi
        ((i++))
    done

    local num_options=${#options[@]}
    local value=""
    while true; do
        if [[ -n "$current" ]]; then
            printf "Choice [%s]: " "$current" >&2
        else
            printf "Choice: " >&2
        fi

        read -r value

        # Empty input: use current default if available
        if [[ -z "$value" ]]; then
            if [[ -n "$current" ]]; then
                value="$current"
                printf '%s\n' "$value"
                return 0
            fi
            warn "Please select an option." >&2
            continue
        fi

        # Check if numeric input
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            if (( value >= 1 && value <= num_options )); then
                local selected="${options[$((value - 1))]}"
                value="${selected%%:*}"
                printf '%s\n' "$value"
                return 0
            else
                warn "Invalid selection. Choose 1-${num_options}." >&2
                continue
            fi
        fi

        # Check if text matches an option value
        for opt in "${options[@]}"; do
            opt_val="${opt%%:*}"
            if [[ "$value" == "$opt_val" ]]; then
                printf '%s\n' "$value"
                return 0
            fi
        done

        warn "Invalid selection '${value}'. Choose 1-${num_options} or type option name." >&2
    done
}

# prompt_yes_no "question" [default_y|default_n]
#
# Ask yes/no question. Return 0 for yes, 1 for no.
# Default is default_n if not specified.
prompt_yes_no() {
    local question="$1"
    local default="${2:-default_n}"

    # Auto-accept: return default without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]]; then
        if [[ "$default" == "default_y" ]]; then return 0; else return 1; fi
    fi

    local hint
    if [[ "$default" == "default_y" ]]; then
        hint="Y/n"
    else
        hint="y/N"
    fi

    while true; do
        printf "%s [%s]: " "$question" "$hint" >&2
        read -r answer

        local lower
        lower="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
        case "$lower" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            "")
                if [[ "$default" == "default_y" ]]; then
                    return 0
                else
                    return 1
                fi
                ;;
            *)
                warn "Please answer y or n." >&2
                ;;
        esac
    done
}