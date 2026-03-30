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
    run "$BIN_DIR/check_security.sh"
    [[ "$status" -le 2 ]]
    assert_output --partial "standalone"
}

@test "check_security.sh --json outputs valid JSON array" {
    run "$BIN_DIR/check_security.sh" --json
    [[ "$status" -le 2 ]]
    [[ "${output}" == "["* ]]
}

@test "check_security.sh --json contains check objects" {
    run "$BIN_DIR/check_security.sh" --json
    [[ "$status" -le 2 ]]
    assert_output --partial '"check":'
    assert_output --partial '"status":'
}

@test "security_check.sh library passes syntax check" {
    run bash -n "$LIB_DIR/security_check.sh"
    assert_success
}