#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "check_system.sh passes syntax check" {
    run bash -n "$BIN_DIR/check_system.sh"
    assert_success
}

@test "check_system.sh --help shows usage" {
    run "$BIN_DIR/check_system.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "--json"
}

@test "check_system.sh --json outputs valid JSON" {
    run "$BIN_DIR/check_system.sh" --json
    # May fail checks but output should still be JSON array
    [[ "${output}" == "["* ]]
}

@test "check_system.sh detects a mode" {
    run "$BIN_DIR/check_system.sh"
    assert_output --partial "Detected mode:"
}