#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "cli.sh passes syntax check" {
    run bash -n "$BIN_DIR/cli.sh"
    assert_success
}

@test "cli.sh --help shows usage" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "install"
    assert_output --partial "health"
    assert_output --partial "pipeline"
}

@test "cli.sh unknown command exits 1" {
    run "$BIN_DIR/cli.sh" nonexistent_command
    assert_failure
    assert_output --partial "Unknown command"
}

@test "all cli modules pass syntax check" {
    for module in "$BIN_DIR"/cli/*.sh; do
        run bash -n "$module"
        assert_success
    done
}