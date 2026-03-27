#!/usr/bin/env bash
#
# Path resolution for bin/ scripts.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_PATHS_LOADED:-}" ]] && return 0
_LIB_PATHS_LOADED=1

# shellcheck disable=SC2034  # Variables used by scripts that source this file
BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$(dirname "$BIN_DIR")"