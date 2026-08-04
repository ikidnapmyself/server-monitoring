#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "cli/cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/cli/cluster.sh"
    assert_success
}

@test "cluster menu offers a single guided setup entry (command prompts hub/agent)" {
    run grep -qE 'manage.py setup_cluster"' "$BIN_DIR/cli/cluster.sh"
    assert_success
    # No duplicate pre-selected --role entries — setup_cluster prompts for the role.
    run grep -qE "setup_cluster --role" "$BIN_DIR/cli/cluster.sh"
    assert_failure
}
