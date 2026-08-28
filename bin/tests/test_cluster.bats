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

@test "cluster.sh no longer references CLUSTER_ENABLED" {
    run grep -E "CLUSTER_ENABLED" "$BIN_DIR/install/cluster.sh"
    assert_failure
}

@test "security_check.sh no longer references CLUSTER_ENABLED" {
    run grep -E "CLUSTER_ENABLED" "$BIN_DIR/lib/security_check.sh"
    assert_failure
}

# ---------------------------------------------------------------------------
# INSTANCE_ID is identity for every role (it keys Node rows and the
# check:{instance_id}:{checker} alert fingerprints).
# ---------------------------------------------------------------------------

# _run_cluster PROJECT ANSWERS...
#
# Drive cluster.sh non-interactively against a throwaway PROJECT_DIR/.env.
# UV_BIN is a harmless stand-in so the agent dry-run cannot touch Django.
# The alarm turns a prompt that outlives its answers (an empty-input loop at
# EOF) into a failure instead of a hung test run.
_run_cluster() {
    local proj="$1"
    shift
    printf '%s\n' "$@" > "$proj/answers"
    # Installer chatter goes to a log, not into the captured output: a prompt
    # loop can emit a great deal of it before the alarm lands.
    perl -e 'alarm 10; exec @ARGV' \
        env PROJECT_DIR="$proj" UV_BIN=/bin/echo \
        bash "$BIN_DIR/install/cluster.sh" \
        < "$proj/answers" > "$proj/install.log" 2>&1
}

@test "cluster.sh writes a non-empty INSTANCE_ID on a hub-only install" {
    local proj
    proj="$(mktemp -d)"
    : > "$proj/.env"

    run _run_cluster "$proj" "y" "hub" ""
    assert_success

    run grep -E '^INSTANCE_ID=.+' "$proj/.env"
    assert_success
}

@test "cluster.sh writes a non-empty INSTANCE_ID on an agent install" {
    local proj
    proj="$(mktemp -d)"
    : > "$proj/.env"

    run _run_cluster "$proj" "y" "agent" "" "https://hub.example.com" "token-abc"
    assert_success

    run grep -E '^INSTANCE_ID=.+' "$proj/.env"
    assert_success
}

@test "cluster.sh default INSTANCE_ID is not a bare hostname" {
    local proj
    proj="$(mktemp -d)"
    : > "$proj/.env"

    _run_cluster "$proj" "y" "hub" ""

    local written
    written="$(grep -E '^INSTANCE_ID=' "$proj/.env" | tail -1 | cut -d= -f2-)"
    refute [ "$written" = "$(hostname)" ]
    # hostname plus a random hex suffix
    [[ "$written" =~ -[0-9a-f]+$ ]]
}

@test "cluster.sh re-run keeps an existing INSTANCE_ID unchanged" {
    local proj
    proj="$(mktemp -d)"
    printf 'INSTANCE_ID=web-03-a1b2c3d4\n' > "$proj/.env"

    _run_cluster "$proj" "y" "hub" ""

    run grep -c '^INSTANCE_ID=' "$proj/.env"
    assert_output "1"
    run grep '^INSTANCE_ID=' "$proj/.env"
    assert_output "INSTANCE_ID=web-03-a1b2c3d4"
}
