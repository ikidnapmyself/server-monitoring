#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
}

@test "BATS is working" {
    run echo "hello"
    assert_success
    assert_output "hello"
}

@test "LIB_DIR points to bin/lib" {
    [[ "$LIB_DIR" == */bin/lib ]]
}