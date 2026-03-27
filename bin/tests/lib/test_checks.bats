#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/checks.sh"
}

@test "command_exists returns 0 for bash" {
    run command_exists bash
    assert_success
}

@test "command_exists returns 1 for nonexistent command" {
    run command_exists definitely_not_a_real_command_xyz
    assert_failure
}

@test "command_exists returns 0 for ls" {
    run command_exists ls
    assert_success
}