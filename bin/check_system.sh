#!/bin/bash
#
# System check script for server-maintanence
# Runs shell-level pre-checks then delegates to manage.py preflight
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SHELL_ONLY=false
DJANGO_ONLY=false

for arg in "$@"; do
    case $arg in
        --shell-only) SHELL_ONLY=true ;;
        --django-only) DJANGO_ONLY=true ;;
        --help|-h)
            echo "Usage: bin/check_system.sh [OPTIONS]"
            echo ""
            echo "Run system checks for server-maintanence."
            echo ""
            echo "Options:"
            echo "  --shell-only   Run only shell-level checks (pre-Django)"
            echo "  --django-only  Run only Django preflight checks"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

passed=0
warned=0
failed=0

check_pass() { echo -e "  ${GREEN}OK${NC}   $1"; ((passed++)) || true; }
check_warn() { echo -e "  ${YELLOW}WARN${NC} $1"; ((warned++)) || true; }
check_fail() { echo -e "  ${RED}ERR${NC}  $1"; ((failed++)) || true; }

# ---- Shell-level checks ----

run_shell_checks() {
    echo -e "\n${BOLD}=== Shell Checks ===${NC}\n"

    # uv installed
    if command -v uv &>/dev/null; then
        check_pass "uv is installed ($(uv --version 2>/dev/null || echo 'unknown'))"
    else
        check_fail "uv is not installed"
    fi

    # Python version
    py_version=$(python3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+')
    if [ -n "$py_version" ]; then
        major=$(echo "$py_version" | cut -d. -f1)
        minor=$(echo "$py_version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            check_pass "Python $py_version (>= 3.10)"
        else
            check_fail "Python $py_version (need >= 3.10)"
        fi
    else
        check_fail "Python 3 not found"
    fi

    # .env exists
    if [ -f "$PROJECT_DIR/.env" ]; then
        check_pass ".env file found"
    else
        check_warn ".env file not found (copy .env.sample to .env)"
    fi

    # .venv exists
    if [ -d "$PROJECT_DIR/.venv" ]; then
        check_pass ".venv directory found (dependencies installed)"
    else
        check_warn ".venv not found (run: uv sync --extra dev)"
    fi

    # Project directory writable
    if touch "$PROJECT_DIR/.check_system_test" 2>/dev/null; then
        rm -f "$PROJECT_DIR/.check_system_test"
        check_pass "Project directory is writable"
    else
        check_warn "Project directory is not writable"
    fi

    # Disk space (>1GB free)
    if command -v df &>/dev/null; then
        free_kb=$(df -k "$PROJECT_DIR" | tail -1 | awk '{print $4}')
        free_gb=$((free_kb / 1024 / 1024))
        if [ "$free_gb" -ge 1 ]; then
            check_pass "Disk space: ${free_gb}GB free"
        else
            check_warn "Low disk space: ${free_gb}GB free (< 1GB)"
        fi
    fi

    echo ""
}

# ---- Django checks ----

run_django_checks() {
    echo -e "${BOLD}=== Django Preflight ===${NC}\n"
    cd "$PROJECT_DIR"
    uv run python manage.py preflight
}

# ---- Main ----

echo -e "\n${BOLD}============================================${NC}"
echo -e "${BOLD}   server-maintanence System Check${NC}"
echo -e "${BOLD}============================================${NC}"

if [ "$DJANGO_ONLY" = true ]; then
    run_django_checks
elif [ "$SHELL_ONLY" = true ]; then
    run_shell_checks
    echo -e "Shell checks: ${passed} passed, ${warned} warning(s), ${failed} error(s)"
else
    run_shell_checks
    run_django_checks
fi