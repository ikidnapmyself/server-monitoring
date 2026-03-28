#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "setup_cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_cron.sh"
    assert_success
}