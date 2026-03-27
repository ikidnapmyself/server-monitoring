#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/logging.sh"
}

@test "info() outputs with [INFO] label" {
    run info "test message"
    assert_success
    assert_output --partial "[INFO]"
    assert_output --partial "test message"
}

@test "success() outputs with [OK] label" {
    run success "test message"
    assert_success
    assert_output --partial "[OK]"
    assert_output --partial "test message"
}

@test "warn() outputs with [WARN] label" {
    run warn "test message"
    assert_success
    assert_output --partial "[WARN]"
    assert_output --partial "test message"
}

@test "error() outputs with [ERROR] label" {
    run error "test message"
    assert_success
    assert_output --partial "[ERROR]"
    assert_output --partial "test message"
}

@test "error() writes to stderr" {
    run bash -c 'source "'"$LIB_DIR/logging.sh"'" && error "stderr test" 2>&1 1>/dev/null'
    assert_output --partial "stderr test"
}

@test "info() handles multiple arguments" {
    run info "hello world"
    assert_success
    assert_output --partial "hello world"
}