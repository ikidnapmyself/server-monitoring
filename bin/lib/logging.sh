#!/usr/bin/env bash
#
# Logging functions for terminal output.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_LOGGING_LOADED:-}" ]] && return 0
_LIB_LOGGING_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/colors.sh"

info()    { printf "%b[INFO]%b  %s\n" "$BLUE" "$NC" "$*"; }
success() { printf "%b[OK]%b    %s\n" "$GREEN" "$NC" "$*"; }
warn()    { printf "%b[WARN]%b  %s\n" "$YELLOW" "$NC" "$*"; }
error()   { printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$*" >&2; }