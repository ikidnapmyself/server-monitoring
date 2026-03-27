#!/usr/bin/env bash
#
# Color constants for terminal output.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_COLORS_LOADED:-}" ]] && return 0
_LIB_COLORS_LOADED=1

export RED=$'\033[0;31m'
export GREEN=$'\033[0;32m'
export YELLOW=$'\033[1;33m'
export BLUE=$'\033[0;34m'
export CYAN=$'\033[0;36m'
export BOLD=$'\033[1m'
export NC=$'\033[0m'