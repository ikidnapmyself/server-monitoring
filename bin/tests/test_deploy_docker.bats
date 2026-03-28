#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "deploy-docker.sh passes syntax check" {
    run bash -n "$BIN_DIR/deploy-docker.sh"
    assert_success
}

@test "deploy-docker.sh exists and is executable" {
    [ -x "$BIN_DIR/deploy-docker.sh" ]
}