#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "install.sh passes syntax check" {
    run bash -n "$BIN_DIR/install.sh"
    assert_success
}

@test "install.sh exists and is executable" {
    [ -x "$BIN_DIR/install.sh" ]
}

@test "install/env.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/env.sh"
    assert_success
}

@test "install/celery.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/celery.sh"
    assert_success
}

@test "install/cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cluster.sh"
    assert_success
}

@test "install/deps.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deps.sh"
    assert_success
}
@test "install/migrate.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/migrate.sh"
    assert_success
}

@test "install/cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cron.sh"
    assert_success
}

@test "install/deploy.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deploy.sh"
    assert_success
}

@test "install/aliases.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/aliases.sh"
    assert_success
}

@test "install.sh help shows available subcommands" {
    run "$BIN_DIR/install.sh" help
    assert_success
    assert_output --partial "env"
    assert_output --partial "celery"
    assert_output --partial "cluster"
    assert_output --partial "deps"
    assert_output --partial "migrate"
    assert_output --partial "cron"
    assert_output --partial "aliases"
    assert_output --partial "deploy"
}

@test "install.sh rejects unknown subcommand" {
    run "$BIN_DIR/install.sh" foobar
    assert_failure
    assert_output --partial "Unknown step"
}
