#!/bin/bash
#
# Security audit for server-maintanence
# Thin wrapper around: python manage.py preflight
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

uv run python manage.py preflight "$@"