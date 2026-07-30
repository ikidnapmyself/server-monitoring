#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "cli/cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/cli/cluster.sh"
    assert_success
}

@test "cluster menu offers guided hub + agent setup" {
    run grep -q "setup_cluster --role hub" "$BIN_DIR/cli/cluster.sh"
    assert_success
    run grep -q "setup_cluster --role agent" "$BIN_DIR/cli/cluster.sh"
    assert_success
}
