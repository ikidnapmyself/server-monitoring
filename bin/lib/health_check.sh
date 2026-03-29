#!/usr/bin/env bash
#
# Unified health check library.
# Auto-detects deployment mode and runs appropriate checks.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_HEALTH_CHECK_LOADED:-}" ]] && return 0
_LIB_HEALTH_CHECK_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/colors.sh"
source "$_LIB_DIR/paths.sh"
source "$_LIB_DIR/checks.sh"

# --- State ---

_hc_passed=0
_hc_warned=0
_hc_failed=0
_hc_json_mode=false
_hc_json_results=()

# --- Result helpers ---

hc_pass() {
    local check="$1" msg="$2"
    if [ "$_hc_json_mode" = true ]; then
        _hc_json_results+=("{\"check\":\"$check\",\"status\":\"ok\",\"message\":\"$msg\"}")
    else
        printf "  %bOK%b   %s\n" "$GREEN" "$NC" "$msg"
    fi
    ((_hc_passed++)) || true
}

hc_warn() {
    local check="$1" msg="$2"
    if [ "$_hc_json_mode" = true ]; then
        _hc_json_results+=("{\"check\":\"$check\",\"status\":\"warn\",\"message\":\"$msg\"}")
    else
        printf "  %bWARN%b %s\n" "$YELLOW" "$NC" "$msg"
    fi
    ((_hc_warned++)) || true
}

hc_fail() {
    local check="$1" msg="$2"
    if [ "$_hc_json_mode" = true ]; then
        _hc_json_results+=("{\"check\":\"$check\",\"status\":\"err\",\"message\":\"$msg\"}")
    else
        printf "  %bERR%b  %s\n" "$RED" "$NC" "$msg"
    fi
    ((_hc_failed++)) || true
}

# --- Mode detection ---

detect_mode() {
    # 1. Docker — compose containers running for this project
    local compose_file="$PROJECT_DIR/deploy/docker/docker-compose.yml"
    if command_exists docker && docker compose -f "$compose_file" ps --format json 2>/dev/null | grep -q "running"; then
        echo "docker"
        return 0
    fi

    # 2. systemd — server-monitoring.service unit exists
    if command -v systemctl &>/dev/null && systemctl list-unit-files server-monitoring.service &>/dev/null 2>&1 && \
       systemctl list-unit-files server-monitoring.service 2>/dev/null | grep -q "server-monitoring"; then
        echo "systemd"
        return 0
    fi

    # 3. prod — .venv exists + DJANGO_ENV=prod in .env
    if [ -d "$PROJECT_DIR/.venv" ] && [ -f "$PROJECT_DIR/.env" ]; then
        if grep -qE "^DJANGO_ENV=prod" "$PROJECT_DIR/.env" 2>/dev/null; then
            echo "prod"
            return 0
        fi
    fi

    # 4. dev — fallback
    echo "dev"
}

# --- Check groups ---

run_core_checks() {
    printf "\n%b=== Core Checks ===%b\n\n" "$BOLD" "$NC"

    # Python 3.10+
    local py_version
    py_version=$(python3 --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' || true)
    if [ -n "$py_version" ]; then
        local major minor
        major=$(echo "$py_version" | cut -d. -f1)
        minor=$(echo "$py_version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            hc_pass "python" "Python $py_version (>= 3.10)"
        else
            hc_fail "python" "Python $py_version (need >= 3.10)"
        fi
    else
        hc_fail "python" "Python 3 not found"
    fi

    # uv installed
    if command_exists uv; then
        hc_pass "uv" "uv is installed ($(uv --version 2>/dev/null || echo 'unknown'))"
    else
        hc_fail "uv" "uv is not installed"
    fi

    # .env exists
    if [ -f "$PROJECT_DIR/.env" ]; then
        hc_pass "dotenv" ".env file found"
    else
        hc_warn "dotenv" ".env file not found (copy .env.sample to .env)"
    fi

    # .venv exists
    if [ -d "$PROJECT_DIR/.venv" ]; then
        hc_pass "venv" ".venv directory found"
    else
        hc_warn "venv" ".venv not found (run: uv sync)"
    fi

    # Project directory writable
    if touch "$PROJECT_DIR/.check_system_test" 2>/dev/null; then
        rm -f "$PROJECT_DIR/.check_system_test"
        hc_pass "writable" "Project directory is writable"
    else
        hc_warn "writable" "Project directory is not writable"
    fi

    # Disk space (>1GB free)
    if command -v df &>/dev/null; then
        local free_kb free_gb
        free_kb=$(df -k "$PROJECT_DIR" | tail -1 | awk '{print $4}')
        free_gb=$((free_kb / 1024 / 1024))
        if [ "$free_gb" -ge 1 ]; then
            hc_pass "disk" "Disk space: ${free_gb}GB free"
        else
            hc_warn "disk" "Low disk space: ${free_gb}GB free (< 1GB)"
        fi
    fi
}

run_django_checks() {
    printf "\n%b=== Django Checks ===%b\n\n" "$BOLD" "$NC"

    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        hc_warn "django" "Skipping Django checks (.venv not found)"
        return 0
    fi

    # Django system check
    if uv run python manage.py check &>/dev/null; then
        hc_pass "django_check" "Django system check passed"
    else
        hc_fail "django_check" "Django system check failed"
    fi

    # Pending migrations
    if uv run python manage.py migrate --check &>/dev/null; then
        hc_pass "migrations" "No pending migrations"
    else
        hc_warn "migrations" "Pending migrations found (run: uv run python manage.py migrate)"
    fi
}

run_dev_checks() {
    printf "\n%b=== Dev Checks ===%b\n\n" "$BOLD" "$NC"

    # Pre-commit hooks
    if [ -f "$PROJECT_DIR/.git/hooks/pre-commit" ]; then
        hc_pass "precommit" "Pre-commit hooks installed"
    else
        hc_warn "precommit" "Pre-commit hooks not installed (run: uv run pre-commit install)"
    fi

    # Shell aliases
    if [ -f "$PROJECT_DIR/bin/aliases.sh" ]; then
        hc_pass "aliases" "Shell aliases configured"
    else
        hc_warn "aliases" "Shell aliases not configured (run: bin/setup_aliases.sh)"
    fi
}

run_docker_checks() {
    printf "\n%b=== Docker Checks ===%b\n\n" "$BOLD" "$NC"

    local compose_file="$PROJECT_DIR/deploy/docker/docker-compose.yml"

    # Docker daemon
    if command_exists docker && docker info &>/dev/null; then
        hc_pass "docker_daemon" "Docker daemon is running"
    else
        hc_fail "docker_daemon" "Docker daemon is not running"
        return 0
    fi

    # docker compose v2
    if docker compose version &>/dev/null; then
        hc_pass "docker_compose" "docker compose v2 available ($(docker compose version --short 2>/dev/null))"
    else
        hc_fail "docker_compose" "docker compose v2 not available"
        return 0
    fi

    # Container health — check each service
    source "$_LIB_DIR/docker.sh"
    local services=("redis" "web" "celery")
    for svc in "${services[@]}"; do
        local state
        state=$(get_service_state "$compose_file" "$svc")
        if [ "$state" = "running" ]; then
            hc_pass "container_$svc" "$svc container is running"
        else
            hc_fail "container_$svc" "$svc container is not running (state: ${state:-unknown})"
        fi
    done
}

run_systemd_checks() {
    printf "\n%b=== systemd Checks ===%b\n\n" "$BOLD" "$NC"

    # server-monitoring.service
    if systemctl is-active --quiet server-monitoring 2>/dev/null; then
        hc_pass "systemd_web" "server-monitoring.service is active"
    else
        hc_fail "systemd_web" "server-monitoring.service is not active"
    fi

    # server-monitoring-celery.service
    if systemctl is-active --quiet server-monitoring-celery 2>/dev/null; then
        hc_pass "systemd_celery" "server-monitoring-celery.service is active"
    else
        hc_fail "systemd_celery" "server-monitoring-celery.service is not active"
    fi

    # Redis
    if systemctl is-active --quiet redis-server 2>/dev/null || systemctl is-active --quiet redis 2>/dev/null; then
        hc_pass "redis" "Redis service is active"
    else
        hc_fail "redis" "Redis service is not active"
    fi

    # Gunicorn socket
    if [ -S /run/server-monitoring/gunicorn.sock ]; then
        hc_pass "socket" "Gunicorn socket exists"
    else
        hc_warn "socket" "Gunicorn socket not found at /run/server-monitoring/gunicorn.sock"
    fi
}

# --- Orchestrator ---

run_all_checks() {
    local mode
    mode=$(detect_mode)

    if [ "$_hc_json_mode" = false ]; then
        printf "\n%b============================================%b\n" "$BOLD" "$NC"
        printf "%b   server-maintanence Health Check%b\n" "$BOLD" "$NC"
        printf "%b============================================%b\n" "$BOLD" "$NC"
        printf "\n  Detected mode: %b%s%b\n" "$CYAN" "$mode" "$NC"
    fi

    case "$mode" in
        dev)
            run_core_checks
            run_django_checks
            run_dev_checks
            ;;
        prod)
            run_core_checks
            run_django_checks
            ;;
        docker)
            run_docker_checks
            ;;
        systemd)
            run_systemd_checks
            ;;
    esac

    if [ "$_hc_json_mode" = true ]; then
        # Output JSON array
        printf "["
        local first=true
        for item in "${_hc_json_results[@]}"; do
            if [ "$first" = true ]; then
                first=false
            else
                printf ","
            fi
            printf "%s" "$item"
        done
        printf "]\n"
    else
        # Summary line
        printf "\n  %b%d passed%b, %b%d warning(s)%b, %b%d error(s)%b\n\n" \
            "$GREEN" "$_hc_passed" "$NC" \
            "$YELLOW" "$_hc_warned" "$NC" \
            "$RED" "$_hc_failed" "$NC"
    fi

    # Exit code: 1 if any errors
    [ "$_hc_failed" -eq 0 ]
}