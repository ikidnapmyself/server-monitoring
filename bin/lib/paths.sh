#!/usr/bin/env bash
#
# Path resolution for bin/ scripts.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_PATHS_LOADED:-}" ]] && return 0
_LIB_PATHS_LOADED=1

if [[ -z "${BIN_DIR:-}" ]]; then
    BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
export BIN_DIR
export PROJECT_DIR="$(dirname "$BIN_DIR")"
export LOG_DIR="${LOGS_DIR:-$PROJECT_DIR/logs}"
mkdir -p "$LOG_DIR"
