#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/pickers.sh"
}

@test "parse_checker_names extracts names after the marker, skipping noise" {
    raw=$'System check identified some issues:\nWARNINGS:\n?: (checkers.W015) noise\nAvailable checkers:\n  cpu                  Check CPU usage.\n  memory               Check memory.\n  disk                 Check disk.'
    run parse_checker_names "$raw"
    assert_success
    assert_line --index 0 "cpu"
    assert_line --index 1 "memory"
    assert_line --index 2 "disk"
    [ "${#lines[@]}" -eq 3 ]
}

@test "parse_checker_names returns nothing when marker absent" {
    run parse_checker_names "no checkers here"
    assert_success
    [ "${#lines[@]}" -eq 0 ]
}

@test "parse_pipeline_names extracts quoted names incl. inactive" {
    raw=$'WARNINGS: noise\n--- Pipeline: "cli-test" ---\n  Flow: ctx\n--- Pipeline: "local-smart-2" ---\n  (inactive)\n  Flow: x'
    run parse_pipeline_names "$raw"
    assert_success
    assert_line --index 0 "cli-test"
    assert_line --index 1 "local-smart-2"
    [ "${#lines[@]}" -eq 2 ]
}

@test "pick_or_cancel non-TTY: selecting Cancel (index 1) returns non-zero" {
    run bash -c 'source bin/lib/tuin.sh; source bin/lib/pickers.sh; printf "1\n" | pick_or_cancel "Pick" alpha beta'
    assert_failure
}

@test "pick_or_cancel non-TTY: selecting first real option returns its value" {
    run bash -c 'source bin/lib/tuin.sh; source bin/lib/pickers.sh; printf "2\n" | pick_or_cancel "Pick" alpha beta'
    assert_success
    assert_output --partial "alpha"
}