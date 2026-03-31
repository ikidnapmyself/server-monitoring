#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "update.sh passes syntax check" {
    run bash -n "$BIN_DIR/update.sh"
    assert_success
}

@test "update library passes syntax check" {
    run bash -n "$LIB_DIR/update.sh"
    assert_success
}

@test "update.sh --help shows usage" {
    run "$BIN_DIR/update.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "--rollback"
    assert_output --partial "--dry-run"
    assert_output --partial "--json"
    assert_output --partial "--auto-env"
}

@test "update.sh --dry-run does not modify repo" {
    local sha_before
    sha_before=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    run "$BIN_DIR/update.sh" --dry-run
    local sha_after
    sha_after=$(git -C "$PROJECT_DIR" rev-parse HEAD)
    assert_equal "$sha_before" "$sha_after"
}

@test "update.sh --dry-run --json outputs JSON" {
    run "$BIN_DIR/update.sh" --dry-run --json
    [[ "${output}" == "{"* ]]
}

@test "setup_cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/setup_cron.sh"
    assert_success
}