#!/usr/bin/env bash
#
# Vendoring helper for tuin (single-file pure-bash TUI library).
# Source this file — do not execute directly.
#
[[ -n "${_LIB_TUIN_VENDOR_LOADED:-}" ]] && return 0
_LIB_TUIN_VENDOR_LOADED=1

_TV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Bump this one line to update tuin, then run `vendor_tuin` and commit.
TUIN_VERSION="${TUIN_VERSION:-v0.1.0}"
TUIN_LOCAL="$_TV_DIR/tuin.sh"
TUIN_URL="https://raw.githubusercontent.com/ikidnapmyself/tuin/${TUIN_VERSION}/tuin.sh"

# vendor_tuin — (re)download the pinned tuin.sh into bin/lib/tuin.sh
vendor_tuin() {
    command -v curl >/dev/null 2>&1 || { echo "curl is required to vendor tuin" >&2; return 1; }
    echo "Fetching tuin ${TUIN_VERSION} -> ${TUIN_LOCAL}" >&2
    local tmp
    tmp="$(mktemp "${TUIN_LOCAL}.XXXXXX")" || { echo "Failed to create temp file" >&2; return 1; }
    if ! curl -fsSL "$TUIN_URL" -o "$tmp"; then
        echo "Failed to fetch tuin" >&2
        rm -f "$tmp"
        return 1
    fi
    if [ ! -s "$tmp" ] || ! grep -q 'Version:' "$tmp"; then
        echo "Fetched tuin looks invalid (empty or missing version marker)" >&2
        rm -f "$tmp"
        return 1
    fi
    mv "$tmp" "$TUIN_LOCAL" || { echo "Failed to install tuin" >&2; rm -f "$tmp"; return 1; }
    echo "tuin ${TUIN_VERSION} vendored." >&2
}

# ensure_tuin — fetch only if missing (self-heal for installs)
ensure_tuin() {
    [[ -f "$TUIN_LOCAL" ]] || vendor_tuin
}