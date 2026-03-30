#!/usr/bin/env bash
#
# Security posture audit library.
# Auto-detects deployment mode and runs appropriate security checks.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_SECURITY_CHECK_LOADED:-}" ]] && return 0
_LIB_SECURITY_CHECK_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/colors.sh"
source "$_LIB_DIR/paths.sh"
source "$_LIB_DIR/checks.sh"

# --- State ---

_sc_passed=0
_sc_warned=0
_sc_failed=0
_sc_json_mode=false
_sc_json_results=()

# --- Mode flags ---

_sc_is_agent=false
_sc_is_hub=false

# --- Result helpers ---

sc_pass() {
    local check="$1" msg="$2"
    if [ "$_sc_json_mode" = true ]; then
        _sc_json_results+=("{\"check\":\"$check\",\"status\":\"pass\",\"message\":\"$msg\"}")
    else
        printf "  %bPASS%b %s\n" "$GREEN" "$NC" "$msg"
    fi
    ((_sc_passed++)) || true
}

sc_warn() {
    local check="$1" msg="$2" fix="${3:-}"
    if [ "$_sc_json_mode" = true ]; then
        if [ -n "$fix" ]; then
            _sc_json_results+=("{\"check\":\"$check\",\"status\":\"warn\",\"message\":\"$msg\",\"fix\":\"$fix\"}")
        else
            _sc_json_results+=("{\"check\":\"$check\",\"status\":\"warn\",\"message\":\"$msg\"}")
        fi
    else
        printf "  %bWARN%b %s\n" "$YELLOW" "$NC" "$msg"
        [ -n "$fix" ] && printf "       %bFix:%b %s\n" "$CYAN" "$NC" "$fix"
    fi
    ((_sc_warned++)) || true
}

sc_fail() {
    local check="$1" msg="$2" fix="${3:-}"
    if [ "$_sc_json_mode" = true ]; then
        if [ -n "$fix" ]; then
            _sc_json_results+=("{\"check\":\"$check\",\"status\":\"fail\",\"message\":\"$msg\",\"fix\":\"$fix\"}")
        else
            _sc_json_results+=("{\"check\":\"$check\",\"status\":\"fail\",\"message\":\"$msg\"}")
        fi
    else
        printf "  %bFAIL%b %s\n" "$RED" "$NC" "$msg"
        [ -n "$fix" ] && printf "       %bFix:%b %s\n" "$CYAN" "$NC" "$fix"
    fi
    ((_sc_failed++)) || true
}

# --- .env reader ---

_sc_env_val() {
    local key="$1"
    local env_file="$PROJECT_DIR/.env"
    if [ -f "$env_file" ]; then
        grep -E "^${key}=" "$env_file" 2>/dev/null | sed "s/^${key}=//" | head -1
    fi
}

# --- Mode detection ---

_sc_detect_modes() {
    local hub_url cluster_enabled
    hub_url=$(_sc_env_val "HUB_URL")
    cluster_enabled=$(_sc_env_val "CLUSTER_ENABLED")

    if [ -n "$hub_url" ]; then
        _sc_is_agent=true
    else
        _sc_is_agent=false
    fi

    if [ "$cluster_enabled" = "1" ]; then
        _sc_is_hub=true
    else
        _sc_is_hub=false
    fi
}

# =====================================================================
# Common security checks
# =====================================================================

_sc_check_secret_key() {
    local key
    key=$(_sc_env_val "DJANGO_SECRET_KEY")
    if [ -z "$key" ]; then
        sc_fail "secret_key" "DJANGO_SECRET_KEY is empty" \
            "Generate a key: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
    elif [ "${#key}" -lt 50 ]; then
        sc_fail "secret_key" "DJANGO_SECRET_KEY is too short (${#key} chars, need >= 50)" \
            "Generate a longer key with at least 50 characters"
    else
        sc_pass "secret_key" "DJANGO_SECRET_KEY is set (${#key} chars)"
    fi
}

_sc_check_debug_mode() {
    local debug env
    debug=$(_sc_env_val "DJANGO_DEBUG")
    env=$(_sc_env_val "DJANGO_ENV")

    if [ "$debug" = "1" ] || [ "$debug" = "true" ] || [ "$debug" = "True" ]; then
        if [ "$env" = "dev" ] || [ -z "$env" ]; then
            sc_pass "debug_mode" "DEBUG is on (acceptable in dev environment)"
        else
            sc_fail "debug_mode" "DEBUG is enabled in $env environment" \
                "Set DJANGO_DEBUG=0 in .env for non-dev environments"
        fi
    else
        sc_pass "debug_mode" "DEBUG is off"
    fi
}

_sc_check_env_permissions() {
    local env_file="$PROJECT_DIR/.env"
    if [ ! -f "$env_file" ]; then
        return 0
    fi

    local perms
    if [[ "$OSTYPE" == "darwin"* ]]; then
        perms=$(stat -f "%Lp" "$env_file" 2>/dev/null)
    else
        perms=$(stat -c "%a" "$env_file" 2>/dev/null)
    fi

    if [ -z "$perms" ]; then
        return 0
    fi

    # Check if the last octal digit (others) allows read (>= 4)
    local others_perm
    others_perm=$((perms % 10))
    if [ "$others_perm" -ge 4 ]; then
        sc_warn "env_permissions" ".env is world-readable (mode: $perms)" \
            "Run: chmod 600 $env_file"
    else
        sc_pass "env_permissions" ".env permissions are restricted (mode: $perms)"
    fi
}

_sc_check_allowed_hosts() {
    local hosts
    hosts=$(_sc_env_val "DJANGO_ALLOWED_HOSTS")
    if [ -z "$hosts" ]; then
        sc_warn "allowed_hosts" "DJANGO_ALLOWED_HOSTS is empty" \
            "Set DJANGO_ALLOWED_HOSTS to your domain(s) in .env"
    elif [ "$hosts" = "*" ]; then
        sc_warn "allowed_hosts" "DJANGO_ALLOWED_HOSTS is set to wildcard (*)" \
            "Restrict DJANGO_ALLOWED_HOSTS to specific domain(s)"
    else
        sc_pass "allowed_hosts" "DJANGO_ALLOWED_HOSTS is configured ($hosts)"
    fi
}

_sc_check_dependencies() {
    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        return 0
    fi

    if ! command_exists uv; then
        return 0
    fi

    if ! uv run pip-audit --version &>/dev/null; then
        return 0
    fi

    local audit_output
    if audit_output=$(uv run pip-audit 2>&1); then
        sc_pass "dependencies" "No known vulnerabilities in dependencies"
    else
        local vuln_count
        vuln_count=$(echo "$audit_output" | grep -cE "^[a-zA-Z]" 2>/dev/null || echo "unknown")
        sc_warn "dependencies" "pip-audit found potential vulnerabilities" \
            "Run: uv run pip-audit --fix"
    fi
}

run_common_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Common Security Checks ===%b\n\n" "$BOLD" "$NC"

    _sc_check_secret_key
    _sc_check_debug_mode
    _sc_check_env_permissions
    _sc_check_allowed_hosts
    _sc_check_dependencies
}

# =====================================================================
# Agent-mode security checks
# =====================================================================

_sc_check_hub_tls() {
    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")
    if [[ "$hub_url" == https://* ]]; then
        sc_pass "hub_tls" "HUB_URL uses HTTPS ($hub_url)"
    else
        sc_fail "hub_tls" "HUB_URL does not use HTTPS ($hub_url)" \
            "Change HUB_URL to use https:// in .env"
    fi
}

_sc_check_cluster_secret() {
    local secret
    secret=$(_sc_env_val "WEBHOOK_SECRET_CLUSTER")
    if [ -z "$secret" ]; then
        sc_fail "cluster_secret" "WEBHOOK_SECRET_CLUSTER is empty" \
            "Generate a secret: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    elif [ "${#secret}" -lt 32 ]; then
        sc_fail "cluster_secret" "WEBHOOK_SECRET_CLUSTER is too short (${#secret} chars, need >= 32)" \
            "Generate a longer secret with at least 32 characters"
    else
        sc_pass "cluster_secret" "WEBHOOK_SECRET_CLUSTER is set (${#secret} chars)"
    fi
}

_sc_check_hub_reachable() {
    if ! command_exists curl; then
        return 0
    fi

    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")
    if curl --max-time 5 -sf -o /dev/null "$hub_url" 2>/dev/null; then
        sc_pass "hub_reachable" "HUB_URL is reachable ($hub_url)"
    else
        sc_warn "hub_reachable" "Cannot reach HUB_URL ($hub_url)" \
            "Verify the hub is running and network connectivity is available"
    fi
}

_sc_check_hub_cert() {
    if ! command_exists curl; then
        return 0
    fi

    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")

    if [[ "$hub_url" != https://* ]]; then
        return 0
    fi

    if curl --max-time 5 -sf -o /dev/null "$hub_url" 2>/dev/null; then
        sc_pass "hub_cert" "TLS certificate for HUB_URL is valid"
    else
        sc_warn "hub_cert" "TLS certificate issue for HUB_URL ($hub_url)" \
            "Check certificate validity and chain of trust"
    fi
}

run_agent_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Agent-Mode Security Checks ===%b\n\n" "$BOLD" "$NC"

    _sc_check_hub_tls
    _sc_check_cluster_secret
    _sc_check_hub_reachable
    _sc_check_hub_cert
}

# =====================================================================
# Hub-mode security checks
# =====================================================================

_sc_check_hub_cluster_secret() {
    local secret
    secret=$(_sc_env_val "WEBHOOK_SECRET_CLUSTER")
    if [ -z "$secret" ]; then
        sc_fail "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is empty — cannot verify agent signatures" \
            "Generate a secret: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    elif [ "${#secret}" -lt 32 ]; then
        sc_fail "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is too short (${#secret} chars) — cannot verify agent signatures" \
            "Generate a longer secret with at least 32 characters"
    else
        sc_pass "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is set for agent verification (${#secret} chars)"
    fi
}

_sc_check_bind_address() {
    local bind_output=""

    if command_exists ss; then
        bind_output=$(ss -tlnp 2>/dev/null | grep -E ":(8000|8080|80|443)\b" || true)
    elif command_exists netstat; then
        bind_output=$(netstat -tlnp 2>/dev/null | grep -E ":(8000|8080|80|443)\b" || true)
    else
        return 0
    fi

    if [ -z "$bind_output" ]; then
        sc_pass "bind_address" "No web server detected on common ports"
        return 0
    fi

    if echo "$bind_output" | grep -q "0\.0\.0\.0"; then
        sc_warn "bind_address" "Web server is bound to 0.0.0.0 (all interfaces)" \
            "Bind to 127.0.0.1 and use a reverse proxy for external traffic"
    else
        sc_pass "bind_address" "Web server is not bound to 0.0.0.0"
    fi
}

_sc_check_reverse_proxy() {
    local found=false

    for proc in nginx caddy apache2 httpd haproxy; do
        if pgrep -x "$proc" &>/dev/null; then
            found=true
            sc_pass "reverse_proxy" "Reverse proxy detected ($proc)"
            return 0
        fi
    done

    if [ "$found" = false ]; then
        sc_warn "reverse_proxy" "No reverse proxy detected (nginx, caddy, apache2, httpd, haproxy)" \
            "Install and configure a reverse proxy for TLS termination and request filtering"
    fi
}

_sc_check_https_termination() {
    # Check nginx TLS config
    if command_exists nginx; then
        if nginx -T 2>/dev/null | grep -q "ssl_certificate"; then
            sc_pass "https_termination" "TLS termination confirmed (nginx ssl_certificate found)"
            return 0
        fi
    fi

    # Check caddy (auto-TLS by default)
    if pgrep -x caddy &>/dev/null; then
        sc_pass "https_termination" "TLS termination likely configured (caddy detected with auto-TLS)"
        return 0
    fi

    sc_warn "https_termination" "Cannot confirm TLS termination configuration" \
        "Ensure your reverse proxy is configured with a valid TLS certificate"
}

run_hub_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Hub-Mode Security Checks ===%b\n\n" "$BOLD" "$NC"

    _sc_check_hub_cluster_secret
    _sc_check_bind_address
    _sc_check_reverse_proxy
    _sc_check_https_termination
}

# =====================================================================
# Orchestrator
# =====================================================================

run_security_audit() {
    _sc_detect_modes

    # Determine mode label
    local mode_label="standalone"
    if [ "$_sc_is_agent" = true ] && [ "$_sc_is_hub" = true ]; then
        mode_label="agent + hub"
    elif [ "$_sc_is_agent" = true ]; then
        mode_label="agent"
    elif [ "$_sc_is_hub" = true ]; then
        mode_label="hub"
    fi

    if [ "$_sc_json_mode" = false ]; then
        printf "\n%b============================================%b\n" "$BOLD" "$NC"
        printf "%b   server-maintanence Security Audit%b\n" "$BOLD" "$NC"
        printf "%b============================================%b\n" "$BOLD" "$NC"
        printf "\n  Detected mode: %b%s%b\n" "$CYAN" "$mode_label" "$NC"
    fi

    run_common_checks

    if [ "$_sc_is_agent" = true ]; then
        run_agent_checks
    fi

    if [ "$_sc_is_hub" = true ]; then
        run_hub_checks
    fi

    if [ "$_sc_json_mode" = true ]; then
        printf "["
        local first=true
        for item in "${_sc_json_results[@]}"; do
            if [ "$first" = true ]; then
                first=false
            else
                printf ","
            fi
            printf "%s" "$item"
        done
        printf "]\n"
    else
        printf "\n  %b%d passed%b, %b%d warning(s)%b, %b%d failure(s)%b\n\n" \
            "$GREEN" "$_sc_passed" "$NC" \
            "$YELLOW" "$_sc_warned" "$NC" \
            "$RED" "$_sc_failed" "$NC"
    fi

    # Return code: 0 = all pass, 1 = warnings only, 2 = failures
    if [ "$_sc_failed" -gt 0 ]; then
        return 2
    elif [ "$_sc_warned" -gt 0 ]; then
        return 1
    else
        return 0
    fi
}