#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "pipeline.sh passes syntax check" {
    run bash -n "$BIN_DIR/cli/pipeline.sh"
    assert_success
}

@test "pipeline menu no longer surfaces pipeline-definition operations" {
    # Definitions are an advanced/optional path; the default menu must not push them.
    run grep -E "show_pipeline|run_pipeline --definition|by definition" "$BIN_DIR/cli/pipeline.sh"
    assert_failure
}

@test "pipeline menu still offers the default pipeline actions" {
    run grep -q "run_pipeline --sample" "$BIN_DIR/cli/pipeline.sh"
    assert_success
    run grep -q "run_pipeline --checks-only" "$BIN_DIR/cli/pipeline.sh"
    assert_success
    run grep -q "monitor_pipeline" "$BIN_DIR/cli/pipeline.sh"
    assert_success
}
