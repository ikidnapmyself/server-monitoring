#!/usr/bin/env bash
#
# Color constants for terminal output.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_COLORS_LOADED:-}" ]] && return 0
_LIB_COLORS_LOADED=1

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'