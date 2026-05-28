#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup

    # Color vars used by cluster.sh — defined empty so the test output is plain.
    BOLD=""; NC=""; RED=""; CYAN=""; YELLOW=""; GREEN=""

    # Stub the cli.sh-provided helpers. confirm_and_run normally eval's the
    # command after a y/n prompt; here we just echo it so we can assert which
    # `manage.py` subcommand the menu item dispatched to.
    show_banner() { :; }
    confirm_and_run() { echo "RAN: $*"; }

    # shellcheck disable=SC1090
    source "$BIN_DIR/cli/cluster.sh"
}

@test "cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/cli/cluster.sh"
    assert_success
}

@test "cluster_menu prints header" {
    run cluster_menu <<< "11"
    assert_success
    assert_output --partial "Cluster"
    assert_output --partial "Manage cluster log-push destinations"
}

@test "menu choice 1 (Add destination) dispatches to cluster_dest_add" {
    run cluster_menu <<< "$(printf '1\ntest\nhttps://hub.example.com\nhub-key\n\nn\n')"
    assert_success
    assert_output --partial "cluster_dest_add"
    assert_output --partial "--name test"
    assert_output --partial "--url https://hub.example.com"
    assert_output --partial "--api-key hub-key"
}

@test "menu choice 1 with forward=y includes --forward flag" {
    run cluster_menu <<< "$(printf '1\ntest\nhttps://hub.example.com\nhub-key\nlogs\ny\n')"
    assert_success
    assert_output --partial "cluster_dest_add"
    assert_output --partial "--streams logs"
    assert_output --partial "--forward"
}

@test "menu choice 1 with empty name aborts without dispatching" {
    run cluster_menu <<< "$(printf '1\n\n')"
    assert_success
    refute_output --partial "RAN:"
    assert_output --partial "Name required"
}

@test "menu choice 2 (List destinations) dispatches to cluster_dest_list" {
    run cluster_menu <<< "2"
    assert_success
    assert_output --partial "cluster_dest_list"
}

@test "menu choice 3 (Show details) dispatches to cluster_dest_show with positional name" {
    run cluster_menu <<< "$(printf '3\ncentral\n')"
    assert_success
    assert_output --partial "cluster_dest_show central"
    refute_output --partial "--name"
}

@test "menu choice 4 (Remove destination) dispatches to cluster_dest_remove" {
    run cluster_menu <<< "$(printf '4\ncentral\nn\n')"
    assert_success
    assert_output --partial "cluster_dest_remove --name central"
    refute_output --partial "--hard"
}

@test "menu choice 4 with --hard answer adds the flag" {
    run cluster_menu <<< "$(printf '4\ncentral\ny\n')"
    assert_success
    assert_output --partial "cluster_dest_remove --name central --hard"
}

@test "menu choice 5 (Toggle) dispatches to cluster_dest_toggle with --name" {
    run cluster_menu <<< "$(printf '5\ncentral\n')"
    assert_success
    assert_output --partial "cluster_dest_toggle --name central"
}

@test "menu choice 6 (Forward policy) dispatches to cluster_dest_forward with state" {
    run cluster_menu <<< "$(printf '6\ncentral\non\n')"
    assert_success
    assert_output --partial "cluster_dest_forward --name central on"
}

@test "menu choice 6 with invalid state rejects without dispatching" {
    run cluster_menu <<< "$(printf '6\ncentral\nmaybe\n')"
    assert_success
    refute_output --partial "RAN:"
    assert_output --partial "State must be"
}

@test "menu choice 7 (Test destination) dispatches to cluster_dest_doctor" {
    run cluster_menu <<< "$(printf '7\ncentral\n')"
    assert_success
    assert_output --partial "cluster_dest_doctor central"
}

@test "menu choice 8 (Cluster status) prints PR 2 stub" {
    run cluster_menu <<< "8"
    assert_success
    refute_output --partial "RAN:"
    assert_output --partial "PR 2"
}

@test "menu choice 9 (Push logs now) prints PR 2 stub" {
    run cluster_menu <<< "9"
    assert_success
    refute_output --partial "RAN:"
    assert_output --partial "PR 2"
}

@test "menu choice 10 (Alerts: push to hub) opens push submenu and dispatches" {
    run cluster_menu <<< "$(printf '10\n1\n')"
    assert_success
    assert_output --partial "push_to_hub"
    refute_output --partial "--dry-run"
}

@test "menu choice 10 sub-option 2 includes --dry-run" {
    run cluster_menu <<< "$(printf '10\n2\n')"
    assert_success
    assert_output --partial "push_to_hub --dry-run"
}

@test "menu choice 10 sub-option 3 prompts for checkers and includes --checkers" {
    # printf %q may escape the comma (cpu\,memory) for shell safety; either form
    # eval's back to the same argument.
    run cluster_menu <<< "$(printf '10\n3\ncpu,memory\n')"
    assert_success
    assert_output --partial "push_to_hub --checkers cpu"
    assert_output --partial "memory"
}

@test "menu choice 10 sub-option 3 with empty checkers rejects without dispatching" {
    run cluster_menu <<< "$(printf '10\n3\n\n')"
    assert_success
    refute_output --partial "RAN:"
    assert_output --partial "Checker names required"
}

@test "menu choice 11 (Back) returns without dispatching" {
    run cluster_menu <<< "11"
    assert_success
    refute_output --partial "RAN:"
}

@test "menu invalid choice prints error" {
    run cluster_menu <<< "99"
    assert_success
    assert_output --partial "Invalid option"
}

@test "cli.sh still sources cluster.sh cleanly" {
    run bash -n "$BIN_DIR/cli.sh"
    assert_success
}