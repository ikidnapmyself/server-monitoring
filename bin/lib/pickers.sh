#!/usr/bin/env bash
# Sourced by cli.sh — picker helpers for tuin-based menus. Do not execute directly.
[[ -n "${_LIB_PICKERS_LOADED:-}" ]] && return 0
_LIB_PICKERS_LOADED=1

# parse_checker_names <raw-output-of:check_health --list>
# Emits one checker name per line. Anchors on the "Available checkers:" marker
# (skipping Django system-check noise) and stops at the first blank line.
parse_checker_names() {
    awk '
        /^Available checkers:/ { grab=1; next }
        grab && /^[[:space:]]*$/ { grab=0 }
        grab && /^[[:space:]]+[^[:space:]]/ { print $1 }
    ' <<<"$1"
}

# parse_pipeline_names <raw-output-of:show_pipeline --all>
# Emits one definition name per line (including inactive ones), in order.
parse_pipeline_names() {
    sed -nE 's/^--- Pipeline: "([^"]+)" ---.*/\1/p' <<<"$1"
}

# pick_or_cancel <title> <option...>
# Shows a tuin_choose picker with a prepended "← Cancel" entry.
# Prints the chosen value to stdout, or returns non-zero on Cancel/Ctrl-C/empty.
pick_or_cancel() {
    local title="$1"; shift
    [ "$#" -eq 0 ] && return 1
    [ -n "$title" ] && tuin_section "$title" >&2
    local choice
    choice="$(tuin_choose "← Cancel" "$@")" || return 1
    [ "$choice" = "← Cancel" ] && return 1
    printf '%s\n' "$choice"
}