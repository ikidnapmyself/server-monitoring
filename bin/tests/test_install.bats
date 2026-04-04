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
