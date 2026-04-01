#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/health_check.sh"
    # Reset counters before each test
    _hc_passed=0
    _hc_warned=0
    _hc_failed=0
    _hc_json_results=()
    _hc_json_mode=false
}

# --- Result helpers ---

@test "hc_pass increments passed counter" {
    hc_pass "test check" "all good"
    [ "$_hc_passed" -eq 1 ]
}

@test "hc_warn increments warned counter" {
    hc_warn "test check" "something off"
    [ "$_hc_warned" -eq 1 ]
}

@test "hc_fail increments failed counter" {
    hc_fail "test check" "broken"
    [ "$_hc_failed" -eq 1 ]
}

@test "hc_pass in JSON mode appends to results array" {
    _hc_json_mode=true
    hc_pass "uv" "uv is installed"
    [ "${#_hc_json_results[@]}" -eq 1 ]
    [[ "${_hc_json_results[0]}" == *'"status":"ok"'* ]]
    [[ "${_hc_json_results[0]}" == *'"check":"uv"'* ]]
}

@test "hc_fail in JSON mode appends err status" {
    _hc_json_mode=true
    hc_fail "python" "not found"
    [[ "${_hc_json_results[0]}" == *'"status":"err"'* ]]
}

# --- detect_env ---

@test "detect_env returns dev as fallback (no .env)" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    PROJECT_DIR="$tmpdir"
    run detect_env
    rm -rf "$tmpdir"
    assert_output "dev"
}

@test "detect_env returns dev when DJANGO_ENV=dev in .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DJANGO_ENV=dev" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    run detect_env
    rm -rf "$tmpdir"
    assert_output "dev"
}

@test "detect_env returns prod when DJANGO_ENV=prod in .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DJANGO_ENV=prod" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    run detect_env
    rm -rf "$tmpdir"
    assert_output "prod"
}

@test "detect_env returns dev for unknown DJANGO_ENV value" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DJANGO_ENV=staging" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    run detect_env
    rm -rf "$tmpdir"
    assert_output "dev"
}

# --- detect_deploy_method ---

@test "detect_deploy_method returns bare as fallback (no .env, no Docker)" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    run detect_deploy_method
    rm -rf "$tmpdir"
    assert_output "bare"
}

@test "detect_deploy_method returns bare when DEPLOY_METHOD=bare in .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DEPLOY_METHOD=bare" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    run detect_deploy_method
    rm -rf "$tmpdir"
    assert_output "bare"
}

@test "detect_deploy_method returns docker when DEPLOY_METHOD=docker in .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DEPLOY_METHOD=docker" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    run detect_deploy_method
    rm -rf "$tmpdir"
    assert_output "docker"
}

@test "detect_deploy_method returns bare for unknown DEPLOY_METHOD value" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DEPLOY_METHOD=k8s" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    run detect_deploy_method
    rm -rf "$tmpdir"
    assert_output "bare"
}

# --- detect_mode (backward-compatibility) ---

@test "detect_mode returns dev as fallback" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    systemctl() { return 1; }
    export -f systemctl
    run detect_mode
    rm -rf "$tmpdir"
    assert_success
    assert_output "dev"
}

@test "detect_mode returns dev when .env has DJANGO_ENV=dev" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DJANGO_ENV=dev" > "$tmpdir/.env"
    echo "DEPLOY_METHOD=bare" >> "$tmpdir/.env"
    mkdir -p "$tmpdir/.venv"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    systemctl() { return 1; }
    export -f systemctl
    run detect_mode
    rm -rf "$tmpdir"
    assert_output "dev"
}

@test "detect_mode returns prod when .env has DJANGO_ENV=prod and DEPLOY_METHOD=bare" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DJANGO_ENV=prod" > "$tmpdir/.env"
    echo "DEPLOY_METHOD=bare" >> "$tmpdir/.env"
    mkdir -p "$tmpdir/.venv"
    PROJECT_DIR="$tmpdir"
    docker() { return 1; }
    export -f docker
    systemctl() { return 1; }
    export -f systemctl
    run detect_mode
    rm -rf "$tmpdir"
    assert_output "prod"
}

@test "detect_mode returns docker when DEPLOY_METHOD=docker in .env" {
    local tmpdir
    tmpdir="$(mktemp -d)"
    echo "DEPLOY_METHOD=docker" > "$tmpdir/.env"
    PROJECT_DIR="$tmpdir"
    run detect_mode
    rm -rf "$tmpdir"
    assert_output "docker"
}
