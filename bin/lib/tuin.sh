#!/usr/bin/env bash
#
# tuin.sh — pure-bash TUI primitives, MIT-licensed
# Version: 0.3.0
# Home: https://github.com/ikidnapmyself/tuin
#
# Public API:
#   tuin_choose <opt1> <opt2> ...    Arrow-key menu; type-ahead when >=10 items
#   tuin_confirm <prompt> [default]  Single-keypress y/n
#   tuin_input <prompt> [default] [regex]   Read with default + regex validation
#   tuin_spin <label> -- <cmd> [args ...]   Run command with spinner
#   tuin_banner <title>              Boxed banner
#   tuin_section <heading>           Section divider
#   tuin_version                     Print version string and return 0
#   tuin_unpriv                      Refuse to run as root / via sudo
#   tuin_guard <cmd> [args ...]      Reject privilege-escalating commands
#
# Contract:
#   - Primitives degrade gracefully when stdout or stdin is not a TTY.
#   - Respects NO_COLOR (https://no-color.org).
#   - Bash 3.2+ compatible (macOS default shell).
#   - Zero external dependencies beyond bash, printf, read, stty, tput.
#
# Known v0.1.0 limitation — Ctrl-C in tuin_choose:
#   On bash 3.2, the read builtin auto-restarts after a signal-triggered
#   trap returns, so the first Ctrl-C restores cursor + stty cleanly but
#   leaves the menu waiting; a second Ctrl-C is needed to actually exit.
#   Cursor and terminal state are always restored on the first press.
#
# License: MIT (see LICENSE)
#   Copyright (c) 2026 Burak
#

[[ -n "${_TUIN_LOADED:-}" ]] && return 0
_TUIN_LOADED=1

_TUIN_VERSION="0.3.0"

# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------

_tuin_is_tty() {
    [[ -t 1 && -t 0 && -n "${TERM:-}" && "$TERM" != "dumb" ]]
}

_tuin_use_color() {
    _tuin_is_tty && [[ -z "${NO_COLOR:-}" ]]
}

_tuin_is_utf8() {
    case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
        *[Uu][Tt][Ff]-8 | *[Uu][Tt][Ff]8) return 0 ;;
        *) return 1 ;;
    esac
}

# euid as a function so tests can redefine it to simulate root.
_tuin_euid() {
    printf '%s\n' "${EUID:-0}"
}

# Source-time color detection. Vars stay empty if not using color.
_TUIN_CYAN=""
_TUIN_BOLD=""
_TUIN_REV=""
_TUIN_RESET=""
if _tuin_use_color; then
    if command -v tput >/dev/null 2>&1; then
        _TUIN_CYAN=$(tput setaf 6 2>/dev/null  || printf '\033[36m')
        _TUIN_BOLD=$(tput bold     2>/dev/null || printf '\033[1m')
        _TUIN_REV=$(tput rev       2>/dev/null || printf '\033[7m')
        _TUIN_RESET=$(tput sgr0    2>/dev/null || printf '\033[0m')
    else
        _TUIN_CYAN=$'\033[36m'
        _TUIN_BOLD=$'\033[1m'
        _TUIN_REV=$'\033[7m'
        _TUIN_RESET=$'\033[0m'
    fi
fi

# ---------------------------------------------------------------------------
# Public API — stubs (filled in by later tasks)
# ---------------------------------------------------------------------------

tuin_version() {
    printf '%s\n' "$_TUIN_VERSION"
}

# Refuse to run elevated. Returns non-zero (and warns on stderr) when the
# process is root or was launched via sudo; returns 0 otherwise.
# Idiom:  tuin_unpriv || exit 1
tuin_unpriv() {
    if [[ "$(_tuin_euid)" -eq 0 ]] || [[ -n "${SUDO_USER:-}${SUDO_UID:-}" ]]; then
        printf 'tuin: refusing to run with elevated privileges (root/sudo)\n' >&2
        return 1
    fi
    return 0
}

# Screen a command for privilege escalation. Inspects only the basename of
# argv[0] against a small denylist. Returns non-zero (and warns on stderr)
# for an escalating command, or when called with no command at all; returns 0
# otherwise.
# Idiom:  tuin_guard "$@" && tuin_spin "Running" -- "$@"
tuin_guard() {
    if [[ $# -eq 0 ]]; then
        printf 'tuin: guard called with no command\n' >&2
        return 1
    fi
    local cmd_base="${1:-}"
    cmd_base="${cmd_base##*/}"
    case "$cmd_base" in
        sudo|doas|su|pkexec|run0|sudoedit)
            printf 'tuin: refusing to run escalating command: %s\n' "$cmd_base" >&2
            return 1
            ;;
    esac
    return 0
}

tuin_banner() {
    local title="$1"
    local width=$(( ${#title} + 4 ))
    local i bar=""
    if _tuin_is_utf8; then
        for (( i=0; i<width; i++ )); do bar="${bar}═"; done
        printf '%s╔%s╗%s\n' "$_TUIN_CYAN" "$bar" "$_TUIN_RESET"
        printf '%s║  %s  ║%s\n' "$_TUIN_CYAN" "$title" "$_TUIN_RESET"
        printf '%s╚%s╝%s\n' "$_TUIN_CYAN" "$bar" "$_TUIN_RESET"
    else
        for (( i=0; i<width; i++ )); do bar="${bar}-"; done
        printf '%s+%s+%s\n' "$_TUIN_CYAN" "$bar" "$_TUIN_RESET"
        printf '%s|  %s  |%s\n' "$_TUIN_CYAN" "$title" "$_TUIN_RESET"
        printf '%s+%s+%s\n' "$_TUIN_CYAN" "$bar" "$_TUIN_RESET"
    fi
}

tuin_section() {
    local heading="$1"
    if _tuin_is_utf8; then
        printf '%s═══ %s%s%s ═══%s\n' \
            "$_TUIN_CYAN" "$_TUIN_BOLD" "$heading" "$_TUIN_RESET" "$_TUIN_RESET"
    else
        printf '=== %s ===\n' "$heading"
    fi
}

tuin_confirm() {
    local prompt="$1"
    local default="${2:-n}"
    local indicator key

    if [[ "$default" == "y" || "$default" == "Y" ]]; then
        indicator="[Y/n]"
    else
        indicator="[y/N]"
    fi

    if ! _tuin_is_tty; then
        IFS= read -r key || key=""
        case "$key" in
            y*|Y*) return 0 ;;
            "")
                [[ "$default" == "y" || "$default" == "Y" ]] && return 0
                return 1
                ;;
            *) return 1 ;;
        esac
    fi

    printf '%s %s ' "$prompt" "$indicator"
    IFS= read -rsn1 key
    printf '\n'
    case "$key" in
        y|Y) return 0 ;;
        n|N) return 1 ;;
        ""|$'\n')
            [[ "$default" == "y" || "$default" == "Y" ]] && return 0
            return 1
            ;;
        *) return 1 ;;
    esac
}

tuin_input() {
    local prompt="$1"
    local default="${2:-}"
    local regex="${3:-}"
    local built_prompt value

    if [[ -n "$default" ]]; then
        built_prompt="$prompt [$default]: "
    else
        built_prompt="$prompt: "
    fi

    if ! _tuin_is_tty; then
        IFS= read -r value || value=""
        [[ -z "$value" ]] && value="$default"
        printf '%s\n' "$value"
        return 0
    fi

    while :; do
        IFS= read -r -p "$built_prompt" value
        [[ -z "$value" ]] && value="$default"
        if [[ -z "$regex" ]] || [[ "$value" =~ $regex ]]; then
            printf '%s\n' "$value"
            return 0
        fi
        printf '  invalid; expected match /%s/\n' "$regex" >&2
    done
}

tuin_spin() {
    local label="$1"
    shift
    if [[ "${1:-}" == "--" ]]; then
        shift
    fi

    if ! _tuin_is_tty; then
        "$@"
        return $?
    fi

    local frames i pid rc
    if _tuin_is_utf8; then
        frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    else
        # shellcheck disable=SC1003  # intentional literal backslash spinner frame
        frames='|/-\'
    fi

    printf '\033[?25l'  # hide cursor
    trap 'printf "\r\033[K\033[?25h"; trap - INT TERM; return 130' INT TERM
    "$@" &
    pid=$!
    i=0
    while kill -0 "$pid" 2>/dev/null; do
        if _tuin_is_utf8; then
            printf '\r%s %s' "${frames:$((i % 10)):1}" "$label"
        else
            printf '\r%s %s' "${frames:$((i % 4)):1}" "$label"
        fi
        sleep 0.1
        i=$((i + 1))
    done
    wait "$pid"
    rc=$?
    printf '\r\033[K\033[?25h'  # clear line, show cursor
    trap - INT TERM
    return $rc
}

# True when a human is at stdin (-t 0) and /dev/tty is usable for the UI.
# Note: deliberately does NOT require stdout to be a TTY, so the selected value
# can be captured (choice=$(tuin_choose ...)) while the UI still renders.
_tuin_choose_interactive() {
    [[ -t 0 ]] \
        && [[ -n "${TERM:-}" ]] && [[ "$TERM" != "dumb" ]] \
        && [[ -r /dev/tty && -w /dev/tty ]]
}

tuin_choose() {
    if [[ "$#" -eq 0 ]]; then
        return 2
    fi
    local options=("$@")
    local count="${#options[@]}"

    # Interactive only when a human is at stdin AND /dev/tty opens on fd 3.
    local interactive=0
    if _tuin_choose_interactive && exec 3<>/dev/tty 2>/dev/null; then
        interactive=1
    fi

    if (( ! interactive )); then
        local pick
        IFS= read -r pick || pick=""
        if [[ "$pick" =~ ^[1-9][0-9]*$ ]] && [[ "$pick" -le "$count" ]]; then
            printf '%s\n' "${options[$((pick - 1))]}"
        else
            printf '%s\n' "${options[0]}"
        fi
        return 0
    fi

    # Interactive path: UI + keys via /dev/tty (fd 3); value via stdout (fd 1).
    local selected=0
    local saved_stty
    saved_stty=$(stty -g <&3)
    stty -icanon -echo min 1 time 0 <&3 2>/dev/null

    local _tuin_interrupted=0
    trap '_tuin_interrupted=1; printf "\r\033[K\033[?25h" >&3; stty "$saved_stty" <&3 2>/dev/null; trap - INT TERM' INT TERM

    local filter=""
    local filter_enabled=0
    (( count >= 10 )) && filter_enabled=1
    local filtered_indices=()
    local i
    for (( i=0; i<count; i++ )); do
        filtered_indices+=("$i")
    done
    local visible_count="${#filtered_indices[@]}"

    printf '\033[?25l' >&3  # hide cursor
    _tuin_choose_render >&3

    local last_height=$visible_count
    (( filter_enabled )) && last_height=$((last_height + 1))

    local key seq
    while :; do
        IFS= read -rsn1 key <&3
        if (( _tuin_interrupted )); then
            exec 3<&-
            return 130
        fi
        case "$key" in
            $'\033')
                if read -rsn2 -t 1 seq <&3 2>/dev/null; then
                    case "$seq" in
                        "[A") (( selected > 0 )) && selected=$((selected - 1)) ;;
                        "[B") (( selected < visible_count - 1 )) && selected=$((selected + 1)) ;;
                    esac
                else
                    if (( filter_enabled )) && [[ -n "$filter" ]]; then
                        filter=""
                        _tuin_choose_apply_filter
                    else
                        printf '\033[?25h' >&3
                        stty "$saved_stty" <&3 2>/dev/null
                        trap - INT TERM
                        exec 3<&-
                        return 1
                    fi
                fi
                ;;
            "")
                printf '\033[?25h' >&3
                stty "$saved_stty" <&3 2>/dev/null
                trap - INT TERM
                if (( visible_count == 0 )); then
                    exec 3<&-
                    return 1
                fi
                printf '%s\n' "${options[${filtered_indices[$selected]}]}"
                exec 3<&-
                return 0
                ;;
            $'\177')
                if (( filter_enabled )) && [[ -n "$filter" ]]; then
                    filter="${filter%?}"
                    _tuin_choose_apply_filter
                fi
                ;;
            [[:print:]])
                if (( filter_enabled )); then
                    filter="${filter}${key}"
                    _tuin_choose_apply_filter
                fi
                ;;
        esac
        printf '\033[%dA' "$last_height" >&3
        _tuin_choose_render >&3
        local new_height=$visible_count
        (( filter_enabled )) && new_height=$((new_height + 1))
        if (( new_height < last_height )); then
            local extras=$((last_height - new_height))
            local x
            for (( x=0; x<extras; x++ )); do
                printf '\r\033[K\n' >&3
            done
            printf '\033[%dA' "$extras" >&3
        fi
        last_height=$new_height
    done
}

# All three _tuin_choose_* helpers below are module-level for two reasons:
#   1. So they don't get installed into the caller's environment as a side
#      effect of the first tuin_choose call (design constraint #10).
#   2. So tests can invoke _tuin_choose_filter directly.
# _tuin_choose_apply_filter and _tuin_choose_render rely on bash's dynamic
# scoping to read/write the caller's locals (filter, count, options, selected,
# visible_count, filtered_indices, filter_enabled) — they're only meaningful
# when invoked from tuin_choose's scope. _tuin_choose_filter is standalone.
_tuin_choose_apply_filter() {
    filtered_indices=()
    if [[ -z "$filter" ]]; then
        local j
        for (( j=0; j<count; j++ )); do
            filtered_indices+=("$j")
        done
    else
        local line
        while IFS= read -r line; do
            filtered_indices+=("$line")
        done < <(_tuin_choose_filter "$filter" "${options[@]}")
    fi
    visible_count="${#filtered_indices[@]}"
    if (( visible_count == 0 )); then
        selected=0
    elif (( selected >= visible_count )); then
        selected=$((visible_count - 1))
    fi
}

_tuin_choose_render() {
    local pos idx
    for (( pos=0; pos<visible_count; pos++ )); do
        idx="${filtered_indices[$pos]}"
        printf '\r\033[K'
        if (( pos == selected )); then
            printf '%s>%s %s%s%s\n' \
                "$_TUIN_CYAN" "$_TUIN_RESET" \
                "$_TUIN_REV" "${options[$idx]}" "$_TUIN_RESET"
        else
            printf '  %s\n' "${options[$idx]}"
        fi
    done
    if (( filter_enabled )); then
        printf '\r\033[K  filter: %s\n' "$filter"
    fi
}

_tuin_choose_filter() {
    local filter="$1"
    shift
    local i=0 item
    shopt -s nocasematch
    for item in "$@"; do
        if [[ -z "$filter" ]] || [[ "$item" == *"$filter"* ]]; then
            printf '%d\n' "$i"
        fi
        i=$((i + 1))
    done
    shopt -u nocasematch
}

# tuin_menu <title> <option1> [option2 ...]
#
# A looping menu. Renders <title> + options plus an auto-appended Back entry
# (label via ${TUIN_MENU_BACK:-Back}). On an action pick, sets $TUIN_REPLY to
# the chosen label and returns 0 (so a `while tuin_menu ...; do` loop repeats —
# "never dying"). Returns non-zero on Back / ESC / Ctrl-C (interactive) or on
# empty input / EOF / out-of-range (non-interactive), which ends the loop.
# shellcheck disable=SC2034  # TUIN_REPLY is the output global, read by callers
tuin_menu() {
    if [[ "$#" -lt 2 ]]; then
        return 2
    fi
    local title="$1"; shift
    local back="${TUIN_MENU_BACK:-Back}"
    local opts=("$@" "$back")

    if _tuin_choose_interactive; then
        printf '%s\n' "$title" >/dev/tty 2>/dev/null
        local sel rc
        sel=$(tuin_choose "${opts[@]}"); rc=$?
        (( rc != 0 )) && return 1
        [[ "$sel" == "$back" ]] && return 1
        TUIN_REPLY="$sel"
        return 0
    fi

    local i
    printf '%s\n' "$title" >&2
    for (( i=0; i<${#opts[@]}; i++ )); do
        printf '  %d) %s\n' "$((i + 1))" "${opts[$i]}" >&2
    done
    local pick
    IFS= read -r pick || return 1
    [[ -z "$pick" ]] && return 1
    if [[ "$pick" =~ ^[1-9][0-9]*$ ]] && (( pick <= ${#opts[@]} )); then
        local sel="${opts[$((pick - 1))]}"
        [[ "$sel" == "$back" ]] && return 1
        TUIN_REPLY="$sel"
        return 0
    fi
    return 1
}
