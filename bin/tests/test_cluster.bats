#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cluster.sh"
    assert_success
}

@test "cluster.sh no longer references CLUSTER_ROLE or WEBHOOK_SECRET_CLUSTER" {
    run grep -E "CLUSTER_ROLE|WEBHOOK_SECRET_CLUSTER" "$BIN_DIR/install/cluster.sh"
    assert_failure
}

@test "cluster.sh references HUB_API_KEY and create_api_key" {
    run grep -q "HUB_API_KEY" "$BIN_DIR/install/cluster.sh"
    assert_success
    run grep -q "create_api_key" "$BIN_DIR/install/cluster.sh"
    assert_success
}
