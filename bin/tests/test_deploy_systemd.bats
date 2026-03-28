#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "deploy-systemd.sh passes syntax check" {
    run bash -n "$BIN_DIR/deploy-systemd.sh"
    assert_success
}

@test "deploy-systemd.sh exits 1 when not root" {
    if [ "$(id -u)" -eq 0 ]; then
        skip "Running as root, cannot test non-root failure"
    fi
    run bash "$BIN_DIR/deploy-systemd.sh"
    assert_failure
    assert_output --partial "root"
}