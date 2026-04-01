---
title: "2026-03-30 Security Check Script Implementation Plan"
parent: Plans
nav_order: 79739669
---

# Security Check Script Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement `bin/check_security.sh` — a shell script that audits deployment security posture, auto-detects agent/hub/standalone mode, and reports PASS/WARN/FAIL with remediation hints.

**Architecture:** Pure shell script sourcing `bin/lib/` helpers. A new `bin/lib/security_check.sh` library holds all check functions and state, following the exact pattern of `bin/lib/health_check.sh`. The main script `bin/check_security.sh` is a thin wrapper that parses flags and calls the library.

**Tech Stack:** Bash, curl (for TLS checks), existing `bin/lib/` helpers (colors, paths, dotenv, logging, checks).

---

### Task 1: Create the security check library

**Files:**
- Create: `bin/lib/security_check.sh`

**Step 1: Create `bin/lib/security_check.sh` with state, result helpers, and mode detection**

```bash
#!/usr/bin/env bash
#
# Security posture audit library.
# Auto-detects agent/hub/standalone mode and runs relevant checks.
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

# --- Result helpers ---

sc_pass() {
    local check="$1" msg="$2"
    if [ "$_sc_json_mode" = true ]; then
        _sc_json_results+=("{\"check\":\"$check\",\"status\":\"pass\",\"message\":\"$msg\"}")
    else
        printf "  %b[PASS]%b %s\n" "$GREEN" "$NC" "$msg"
    fi
    ((_sc_passed++)) || true
}

sc_warn() {
    local check="$1" msg="$2" fix="${3:-}"
    local display="$msg"
    [ -n "$fix" ] && display="$msg — Fix: $fix"
    if [ "$_sc_json_mode" = true ]; then
        local json="{\"check\":\"$check\",\"status\":\"warn\",\"message\":\"$msg\""
        [ -n "$fix" ] && json="$json,\"fix\":\"$fix\""
        json="$json}"
        _sc_json_results+=("$json")
    else
        printf "  %b[WARN]%b %s\n" "$YELLOW" "$NC" "$display"
    fi
    ((_sc_warned++)) || true
}

sc_fail() {
    local check="$1" msg="$2" fix="${3:-}"
    local display="$msg"
    [ -n "$fix" ] && display="$msg — Fix: $fix"
    if [ "$_sc_json_mode" = true ]; then
        local json="{\"check\":\"$check\",\"status\":\"fail\",\"message\":\"$msg\""
        [ -n "$fix" ] && json="$json,\"fix\":\"$fix\""
        json="$json}"
        _sc_json_results+=("$json")
    else
        printf "  %b[FAIL]%b %s\n" "$RED" "$NC" "$display"
    fi
    ((_sc_failed++)) || true
}

# --- .env reader ---

_sc_env_val() {
    local key="$1"
    local env_file="$PROJECT_DIR/.env"
    if [ -f "$env_file" ]; then
        grep -E "^[[:space:]]*${key}=" "$env_file" | tail -1 | sed "s/^[[:space:]]*${key}=//" | sed 's/^["'\'']//' | sed 's/["'\'']*$//'
    fi
}

# --- Mode detection ---

_sc_detect_modes() {
    _sc_is_agent=false
    _sc_is_hub=false

    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")
    [ -n "$hub_url" ] && _sc_is_agent=true

    local cluster_enabled
    cluster_enabled=$(_sc_env_val "CLUSTER_ENABLED")
    [ "$cluster_enabled" = "1" ] && _sc_is_hub=true
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/security_check.sh`
Expected: no output (clean parse)

**Step 3: Commit**

```bash
git add bin/lib/security_check.sh
git commit -m "feat: add security check library skeleton with state and mode detection"
```

---

### Task 2: Implement common security checks

**Files:**
- Modify: `bin/lib/security_check.sh`

**Step 1: Add common checks section after the mode detection function**

```bash
# --- Common checks (all modes) ---

_sc_check_secret_key() {
    local key
    key=$(_sc_env_val "DJANGO_SECRET_KEY")
    if [ -z "$key" ]; then
        sc_fail "secret_key" "DJANGO_SECRET_KEY is empty" \
            "generate with: python3 -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
    elif [ "${#key}" -lt 50 ]; then
        sc_fail "secret_key" "DJANGO_SECRET_KEY is too short (${#key} chars, need >= 50)" \
            "generate a longer key with: python3 -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\""
    else
        sc_pass "secret_key" "DJANGO_SECRET_KEY is set (${#key} chars)"
    fi
}

_sc_check_debug_mode() {
    local debug
    debug=$(_sc_env_val "DJANGO_DEBUG")
    local env
    env=$(_sc_env_val "DJANGO_ENV")

    # In dev mode, debug is expected
    if [ "$env" = "dev" ] || [ "$env" = "" ]; then
        if [ "$debug" = "1" ] || [ "$debug" = "True" ] || [ "$debug" = "true" ]; then
            sc_pass "debug_mode" "DEBUG is on (dev environment)"
        else
            sc_pass "debug_mode" "DEBUG is off"
        fi
        return
    fi

    # Non-dev: debug must be off
    if [ "$debug" = "1" ] || [ "$debug" = "True" ] || [ "$debug" = "true" ]; then
        sc_fail "debug_mode" "DEBUG is enabled in $env environment" \
            "set DJANGO_DEBUG=0 in .env"
    else
        sc_pass "debug_mode" "DEBUG is off ($env environment)"
    fi
}

_sc_check_env_permissions() {
    local env_file="$PROJECT_DIR/.env"
    if [ ! -f "$env_file" ]; then
        sc_warn "env_perms" ".env file not found" "copy .env.sample to .env"
        return
    fi

    local perms
    if [[ "$OSTYPE" == "darwin"* ]]; then
        perms=$(stat -f "%Lp" "$env_file")
    else
        perms=$(stat -c "%a" "$env_file")
    fi

    local other_read=$((perms % 10))
    if [ "$other_read" -ge 4 ]; then
        sc_warn "env_perms" ".env is world-readable (mode $perms)" \
            "chmod 600 .env"
    else
        sc_pass "env_perms" ".env file permissions are restrictive (mode $perms)"
    fi
}

_sc_check_allowed_hosts() {
    local hosts
    hosts=$(_sc_env_val "DJANGO_ALLOWED_HOSTS")
    if [ -z "$hosts" ]; then
        sc_warn "allowed_hosts" "DJANGO_ALLOWED_HOSTS is empty" \
            "set explicit hostnames in .env (e.g. DJANGO_ALLOWED_HOSTS=myserver.example.com)"
    elif [ "$hosts" = "*" ]; then
        sc_warn "allowed_hosts" "DJANGO_ALLOWED_HOSTS is wildcard (*)" \
            "set explicit hostnames instead of *"
    else
        sc_pass "allowed_hosts" "ALLOWED_HOSTS is set ($hosts)"
    fi
}

_sc_check_dependencies() {
    if [ ! -d "$PROJECT_DIR/.venv" ]; then
        sc_pass "dep_audit" "Dependency audit skipped (.venv not found)"
        return
    fi

    if ! command_exists uv; then
        sc_pass "dep_audit" "Dependency audit skipped (uv not installed)"
        return
    fi

    if uv run pip-audit --strict --desc 2>/dev/null; then
        sc_pass "dep_audit" "No known vulnerabilities in dependencies"
    else
        sc_warn "dep_audit" "Vulnerable dependencies found" \
            "run: uv run pip-audit --strict --desc --fix"
    fi
}

run_common_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Common Security ===%b\n\n" "$BOLD" "$NC"
    _sc_check_secret_key
    _sc_check_debug_mode
    _sc_check_env_permissions
    _sc_check_allowed_hosts
    _sc_check_dependencies
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/security_check.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/security_check.sh
git commit -m "feat: implement common security checks (secret key, debug, env perms, hosts, deps)"
```

---

### Task 3: Implement agent-mode checks

**Files:**
- Modify: `bin/lib/security_check.sh`

**Step 1: Add agent checks section**

```bash
# --- Agent checks (HUB_URL set) ---

_sc_check_hub_tls() {
    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")

    if [[ "$hub_url" == https://* ]]; then
        sc_pass "hub_tls" "HUB_URL uses HTTPS"
    elif [[ "$hub_url" == http://* ]]; then
        sc_fail "hub_tls" "HUB_URL uses plain HTTP ($hub_url)" \
            "change HUB_URL to use https://"
    else
        sc_fail "hub_tls" "HUB_URL has no scheme ($hub_url)" \
            "set HUB_URL=https://your-hub.example.com"
    fi
}

_sc_check_cluster_secret() {
    local secret
    secret=$(_sc_env_val "WEBHOOK_SECRET_CLUSTER")

    if [ -z "$secret" ]; then
        sc_fail "cluster_secret" "WEBHOOK_SECRET_CLUSTER is not set (HMAC signing disabled)" \
            "generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    elif [ "${#secret}" -lt 32 ]; then
        sc_fail "cluster_secret" "WEBHOOK_SECRET_CLUSTER is too short (${#secret} chars, need >= 32)" \
            "generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    else
        sc_pass "cluster_secret" "WEBHOOK_SECRET_CLUSTER is set (${#secret} chars)"
    fi
}

_sc_check_hub_reachable() {
    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")

    # Extract host:port from URL
    local host_port
    host_port=$(echo "$hub_url" | sed -E 's|^https?://||' | sed 's|/.*||')

    local host port
    host=$(echo "$host_port" | cut -d: -f1)
    port=$(echo "$host_port" | grep -o ':[0-9]*' | tr -d ':')

    # Default ports
    if [ -z "$port" ]; then
        if [[ "$hub_url" == https://* ]]; then
            port=443
        else
            port=80
        fi
    fi

    if command_exists curl; then
        if curl -sf --connect-timeout 5 --max-time 10 -o /dev/null "$hub_url" 2>/dev/null; then
            sc_pass "hub_reachable" "Hub is reachable at $host:$port"
        else
            sc_warn "hub_reachable" "Cannot reach hub at $host:$port" \
                "check network connectivity, firewall rules, and that the hub is running"
        fi
    else
        sc_warn "hub_reachable" "Cannot check hub connectivity (curl not installed)"
    fi
}

_sc_check_hub_cert() {
    local hub_url
    hub_url=$(_sc_env_val "HUB_URL")

    # Only relevant for HTTPS
    [[ "$hub_url" != https://* ]] && return

    if ! command_exists curl; then
        sc_warn "hub_cert" "Cannot check TLS certificate (curl not installed)"
        return
    fi

    local output
    if output=$(curl -svI --connect-timeout 5 --max-time 10 "$hub_url" 2>&1); then
        if echo "$output" | grep -q "SSL certificate verify ok"; then
            sc_pass "hub_cert" "Hub TLS certificate is valid"
        elif echo "$output" | grep -qi "SSL certificate problem"; then
            sc_warn "hub_cert" "Hub TLS certificate issue detected" \
                "check certificate expiry and chain with: curl -vI $hub_url"
        else
            sc_pass "hub_cert" "Hub TLS connection established"
        fi
    else
        if echo "$output" | grep -qi "SSL certificate problem\|certificate.*expired\|certificate.*not yet valid"; then
            sc_warn "hub_cert" "Hub TLS certificate is invalid or expired" \
                "check with: curl -vI $hub_url"
        else
            sc_warn "hub_cert" "Cannot verify hub TLS certificate" \
                "check with: curl -vI $hub_url"
        fi
    fi
}

run_agent_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Agent Security ===%b\n\n" "$BOLD" "$NC"
    _sc_check_hub_tls
    _sc_check_cluster_secret
    _sc_check_hub_reachable
    _sc_check_hub_cert
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/security_check.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/security_check.sh
git commit -m "feat: implement agent-mode security checks (TLS, HMAC, reachability, cert)"
```

---

### Task 4: Implement hub-mode checks

**Files:**
- Modify: `bin/lib/security_check.sh`

**Step 1: Add hub checks section**

```bash
# --- Hub checks (CLUSTER_ENABLED=1) ---

_sc_check_hub_cluster_secret() {
    # Same check as agent but different message context
    local secret
    secret=$(_sc_env_val "WEBHOOK_SECRET_CLUSTER")

    if [ -z "$secret" ]; then
        sc_fail "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is not set (cannot verify agent signatures)" \
            "generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\" and share with agents"
    elif [ "${#secret}" -lt 32 ]; then
        sc_fail "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is too short (${#secret} chars, need >= 32)" \
            "generate a stronger secret with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    else
        sc_pass "hub_cluster_secret" "WEBHOOK_SECRET_CLUSTER is set for signature verification (${#secret} chars)"
    fi
}

_sc_check_bind_address() {
    # Heuristic: check if gunicorn/django is bound to 0.0.0.0
    local listening
    if command_exists ss; then
        listening=$(ss -tlnp 2>/dev/null | grep -E ":(8000|8080|80|443)\b" || true)
    elif command_exists netstat; then
        listening=$(netstat -tlnp 2>/dev/null | grep -E ":(8000|8080|80|443)\b" || true)
    else
        sc_pass "bind_address" "Bind address check skipped (ss/netstat not available)"
        return
    fi

    if [ -z "$listening" ]; then
        sc_pass "bind_address" "No web server ports detected (may be behind a socket)"
        return
    fi

    if echo "$listening" | grep -q "0\.0\.0\.0"; then
        sc_warn "bind_address" "Web server is bound to 0.0.0.0 (publicly accessible)" \
            "bind to 127.0.0.1 and use a reverse proxy, or ensure firewall restricts access"
    else
        sc_pass "bind_address" "Web server is not bound to 0.0.0.0"
    fi
}

_sc_check_reverse_proxy() {
    local found_proxy=false
    local proxy_name=""

    for proc in nginx caddy apache2 httpd haproxy; do
        if pgrep -x "$proc" >/dev/null 2>&1; then
            found_proxy=true
            proxy_name="$proc"
            break
        fi
    done

    if [ "$found_proxy" = true ]; then
        sc_pass "reverse_proxy" "Reverse proxy detected ($proxy_name)"
    else
        sc_warn "reverse_proxy" "No reverse proxy detected (nginx, caddy, apache, haproxy)" \
            "deploy behind a reverse proxy for TLS termination and request filtering"
    fi
}

_sc_check_https_termination() {
    # Check if any proxy is configured for TLS
    local has_tls=false

    # Check nginx SSL config
    if command_exists nginx; then
        if nginx -T 2>/dev/null | grep -q "ssl_certificate"; then
            has_tls=true
        fi
    fi

    # Check caddy (Caddyfile typically has auto-TLS)
    if pgrep -x caddy >/dev/null 2>&1; then
        has_tls=true
    fi

    if [ "$has_tls" = true ]; then
        sc_pass "https_termination" "HTTPS termination is configured"
    else
        sc_warn "https_termination" "Cannot confirm HTTPS termination" \
            "ensure your reverse proxy terminates TLS (e.g. nginx ssl_certificate or Caddy auto-TLS)"
    fi
}

run_hub_checks() {
    [ "$_sc_json_mode" = false ] && printf "\n%b=== Hub Security ===%b\n\n" "$BOLD" "$NC"
    _sc_check_hub_cluster_secret
    _sc_check_bind_address
    _sc_check_reverse_proxy
    _sc_check_https_termination
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/security_check.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/security_check.sh
git commit -m "feat: implement hub-mode security checks (secret, bind, proxy, TLS termination)"
```

---

### Task 5: Add orchestrator and JSON output to the library

**Files:**
- Modify: `bin/lib/security_check.sh`

**Step 1: Add the `run_security_audit` orchestrator function at the end of the library**

```bash
# --- Orchestrator ---

run_security_audit() {
    _sc_detect_modes

    if [ "$_sc_json_mode" = false ]; then
        printf "\n%b============================================%b\n" "$BOLD" "$NC"
        printf "%b   server-maintanence Security Audit%b\n" "$BOLD" "$NC"
        printf "%b============================================%b\n" "$BOLD" "$NC"

        local mode_label="standalone"
        if [ "$_sc_is_agent" = true ] && [ "$_sc_is_hub" = true ]; then
            mode_label="agent + hub"
        elif [ "$_sc_is_agent" = true ]; then
            mode_label="agent"
        elif [ "$_sc_is_hub" = true ]; then
            mode_label="hub"
        fi
        printf "\n  Detected mode: %b%s%b\n" "$CYAN" "$mode_label" "$NC"
    fi

    run_common_checks

    if [ "$_sc_is_agent" = true ]; then
        run_agent_checks
    fi

    if [ "$_sc_is_hub" = true ]; then
        run_hub_checks
    fi

    # --- Output ---

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

    # Exit code: 2 if failures, 1 if warnings only, 0 if clean
    if [ "$_sc_failed" -gt 0 ]; then
        return 2
    elif [ "$_sc_warned" -gt 0 ]; then
        return 1
    fi
    return 0
}
```

**Step 2: Verify syntax**

Run: `bash -n bin/lib/security_check.sh`
Expected: no output

**Step 3: Commit**

```bash
git add bin/lib/security_check.sh
git commit -m "feat: add security audit orchestrator with JSON output and exit codes"
```

---

### Task 6: Create the main script

**Files:**
- Create: `bin/check_security.sh`

**Step 1: Create `bin/check_security.sh`**

```bash
#!/bin/bash
#
# Security posture audit for server-maintanence
# Auto-detects agent/hub/standalone mode.
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/security_check.sh"

cd "$PROJECT_DIR"

# Parse flags
for arg in "$@"; do
    case $arg in
        --json) _sc_json_mode=true ;;
        --help|-h)
            echo "Usage: bin/check_security.sh [OPTIONS]"
            echo ""
            echo "Audit the security posture of this deployment."
            echo "Auto-detects mode: agent (HUB_URL set), hub (CLUSTER_ENABLED=1), or standalone."
            echo ""
            echo "Options:"
            echo "  --json         Output as JSON"
            echo "  --help, -h     Show this help"
            exit 0
            ;;
    esac
done

run_security_audit
```

**Step 2: Make executable and verify syntax**

Run: `chmod +x bin/check_security.sh && bash -n bin/check_security.sh`
Expected: no output

**Step 3: Quick smoke test**

Run: `bin/check_security.sh --help`
Expected: usage text with `--json` and mode detection description

**Step 4: Commit**

```bash
git add bin/check_security.sh
git commit -m "feat: add bin/check_security.sh main script"
```

---

### Task 7: Write bats tests

**Files:**
- Create: `bin/tests/test_check_security.bats`

**Step 1: Create bats test file**

```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "check_security.sh passes syntax check" {
    run bash -n "$BIN_DIR/check_security.sh"
    assert_success
}

@test "check_security.sh --help shows usage" {
    run "$BIN_DIR/check_security.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "--json"
    assert_output --partial "agent"
    assert_output --partial "hub"
}

@test "check_security.sh runs without crashing" {
    run "$BIN_DIR/check_security.sh"
    # May exit 1 (warnings) or 2 (failures) in test environment, but should not crash
    [[ "$status" -le 2 ]]
    assert_output --partial "Security Audit"
}

@test "check_security.sh detects standalone mode when no HUB_URL or CLUSTER_ENABLED" {
    # Ensure no agent/hub vars are set
    unset HUB_URL
    unset CLUSTER_ENABLED
    run "$BIN_DIR/check_security.sh"
    [[ "$status" -le 2 ]]
    assert_output --partial "standalone"
}

@test "check_security.sh --json outputs valid JSON" {
    run "$BIN_DIR/check_security.sh" --json
    [[ "$status" -le 2 ]]
    [[ "${output}" == "["* ]]
}

@test "check_security.sh --json contains check objects" {
    run "$BIN_DIR/check_security.sh" --json
    [[ "$status" -le 2 ]]
    # Should contain at least the secret_key check
    assert_output --partial '"check":'
    assert_output --partial '"status":'
}

@test "security_check.sh library passes syntax check" {
    run bash -n "$LIB_DIR/security_check.sh"
    assert_success
}
```

**Step 2: Run the tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_check_security.bats`
Expected: all tests pass (some checks may warn/fail but tests verify structure not security state)

**Step 3: Commit**

```bash
git add bin/tests/test_check_security.bats
git commit -m "test: add bats tests for check_security.sh"
```

---

### Task 8: Update docs

**Files:**
- Modify: `bin/README.md`

**Step 1: Add `check_security.sh` section to `bin/README.md` after the `check_system.sh` usage section (before the Permissions section)**

Add this block:

```markdown
---

### `check_security.sh` — Security Posture Audit

Audits the security configuration of a deployment. Auto-detects whether this node is an agent, hub, or standalone instance.

```bash
# Run security audit
./bin/check_security.sh

# JSON output (for CI or monitoring)
./bin/check_security.sh --json
```

**Mode detection:**
- **Agent** (`HUB_URL` set): checks TLS, HMAC signing, hub reachability, certificate validity
- **Hub** (`CLUSTER_ENABLED=1`): checks HMAC secret, bind address, reverse proxy, HTTPS termination
- **Standalone**: common checks only (secret key, debug mode, .env permissions, allowed hosts, dependency audit)

**Exit codes:** `0` = all pass, `1` = warnings only, `2` = failures present.
```

**Step 2: Also add to the Quick Command Reference table at the top of bin/README.md**

Add row: `sm-check-security | — | — | Security posture audit (agent/hub/standalone)`

Note: This command doesn't have a management command equivalent, so the Alias and App columns are `—`.

**Step 3: Commit**

```bash
git add bin/README.md
git commit -m "docs: add check_security.sh to bin/README.md"
```