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

@test "cli.sh --help mentions cluster jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    assert_output --partial "cluster"
}

@test "cli.sh --help no longer mentions alerts jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    refute_output --partial "alerts"
}

@test "cli.sh --help no longer mentions system jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    refute_output --partial "system"
}

@test "cli.sh alerts is now an unknown command" {
    run "$BIN_DIR/cli.sh" alerts
    assert_failure
    assert_output --partial "Unknown command"
}

@test "cli.sh system is now an unknown command" {
    run "$BIN_DIR/cli.sh" system
    assert_failure
    assert_output --partial "Unknown command"
}

@test "bin/cli/alerts.sh and bin/cli/system.sh do not exist" {
    [ ! -f "$BIN_DIR/cli/alerts.sh" ]
    [ ! -f "$BIN_DIR/cli/system.sh" ]
}

@test "bin/cli/cluster.sh exists" {
    [ -f "$BIN_DIR/cli/cluster.sh" ]
}

@test "aliases template generates new commands (preflight, show-pipeline, push-to-hub)" {
    run grep -cE '^alias \$\{prefix\}-(preflight|show-pipeline|push-to-hub)=' "$BIN_DIR/install/aliases.sh"
    assert_success
    [ "$output" -eq 3 ]
}

@test "aliases template renamed check-and-alert to run-checks-only" {
    run grep -F 'alias ${prefix}-run-checks-only=' "$BIN_DIR/install/aliases.sh"
    assert_success
}

@test "aliases template no longer contains check-and-alert" {
    run grep -F 'alias ${prefix}-check-and-alert=' "$BIN_DIR/install/aliases.sh"
    assert_failure
}
