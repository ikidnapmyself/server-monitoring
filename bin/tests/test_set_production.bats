#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "set_production.sh passes syntax check" {
    run bash -n "$BIN_DIR/set_production.sh"
    assert_success
}

@test "set_production.sh exists and is executable" {
    [ -x "$BIN_DIR/set_production.sh" ]
}

@test "set_production.sh --help shows usage" {
    run "$BIN_DIR/set_production.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "Convert a dev environment to production"
    assert_output --partial "DJANGO_ENV=prod"
    assert_output --partial "DJANGO_DEBUG=0"
}
