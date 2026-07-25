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

# ---------------------------------------------------------------------------
# uv resolution — ALWAYS an absolute path, NEVER a PATH lookup.
#
# The deploy/install scripts must not invoke a bare `uv` (see AGENTS.md #8):
# a PATH that an attacker can influence — or a `uv` binary planted earlier on
# it — would let arbitrary code run inside a privileged deploy. We resolve uv
# once to a validated absolute path and every caller invokes "$UV_BIN".
#
# A deployment may pin UV_BIN explicitly (source of truth). Otherwise we probe
# the CURRENT user's standard install dirs — never $PATH.
# ---------------------------------------------------------------------------

resolve_uv() {
    local candidate="${UV_BIN:-}"

    if [[ -z "$candidate" ]]; then
        local _home="${HOME:-}"
        if [[ -z "$_home" ]]; then
            _home="$(getent passwd "$(id -un 2>/dev/null)" 2>/dev/null | cut -d: -f6)"
        fi
        local d
        for d in "$_home/.local/bin/uv" "$_home/.cargo/bin/uv"; do
            if [[ -x "$d" ]]; then
                candidate="$d"
                break
            fi
        done
    fi

    # Nothing pinned and nothing found: leave UV_BIN empty so check_uv() can
    # install uv and health checks can report it missing. Not fatal here.
    if [[ -z "$candidate" ]]; then
        UV_BIN=""
        export UV_BIN
        return 1
    fi

    # Absolute path required — reject anything that would resolve via PATH.
    if [[ "$candidate" != /* ]]; then
        echo "ERROR: UV_BIN must be an absolute path, got: '$candidate'" >&2
        return 2
    fi

    if [[ ! -x "$candidate" ]]; then
        echo "ERROR: uv is not executable at: $candidate" >&2
        return 2
    fi

    # Tamper guard: refuse a uv that is group- or world-writable, so a lower
    # privilege account cannot swap the binary under a privileged deploy.
    # Degrade gracefully if perms can't be read (must not break the deploy).
    local perms
    perms="$(stat -c '%a' "$candidate" 2>/dev/null || stat -f '%Lp' "$candidate" 2>/dev/null || echo "")"
    if [[ "$perms" =~ ^[0-7]{3,4}$ ]]; then
        local grp="${perms: -2:1}" oth="${perms: -1}"
        if (( (grp & 2) != 0 || (oth & 2) != 0 )); then
            echo "ERROR: refusing group/world-writable uv ($perms): $candidate" >&2
            return 2
        fi
    fi

    UV_BIN="$candidate"
    export UV_BIN
    return 0
}

# Resolve at source time (guard set -e in callers via && / ||).
resolve_uv && _uv_resolve_rc=0 || _uv_resolve_rc=$?
# rc=2 means an explicitly pinned UV_BIN is invalid or the binary is tampered —
# never continue. rc=1 (simply not found) is deferred to check_uv()/health checks,
# unless UV_BIN_STRICT is set (deploys can opt into hard-fail).
if [[ "$_uv_resolve_rc" -eq 2 ]] || { [[ -n "${UV_BIN_STRICT:-}" ]] && [[ "$_uv_resolve_rc" -ne 0 ]]; }; then
    exit 1
fi
unset _uv_resolve_rc
