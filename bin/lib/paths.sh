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

if [[ -z "${PROJECT_DIR:-}" ]]; then
    PROJECT_DIR="$(dirname "$BIN_DIR")"
fi
export PROJECT_DIR

if [[ -z "${LOG_DIR:-}" ]]; then
    LOG_DIR="${LOGS_DIR:-$PROJECT_DIR/logs}"
fi
export LOG_DIR
mkdir -p "$LOG_DIR"
