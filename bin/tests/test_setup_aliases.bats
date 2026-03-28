#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "setup_aliases.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_aliases.sh"
    assert_success
}

@test "setup_aliases.sh --help shows usage" {
    run "$BIN_DIR/setup_aliases.sh" --help
    assert_success
    assert_output --partial "Usage"
}

@test "setup_aliases.sh --list without aliases file warns" {
    rm -f "$BIN_DIR/aliases.sh"
    run "$BIN_DIR/setup_aliases.sh" --list
    assert_failure
    assert_output --partial "No aliases file"
}