#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "install.sh passes syntax check" {
    run bash -n "$BIN_DIR/install.sh"
    assert_success
}

@test "install.sh exists and is executable" {
    [ -x "$BIN_DIR/install.sh" ]
}

@test "install/env.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/env.sh"
    assert_success
}

@test "install/cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cluster.sh"
    assert_success
}

@test "install/deps.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deps.sh"
    assert_success
}
@test "install/migrate.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/migrate.sh"
    assert_success
}

@test "install/cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cron.sh"
    assert_success
}

@test "install/deploy.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deploy.sh"
    assert_success
}

@test "install/aliases.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/aliases.sh"
    assert_success
}

@test "install.sh help shows available subcommands" {
    run "$BIN_DIR/install.sh" help
    assert_success
    assert_output --partial "env"
    assert_output --partial "cluster"
    assert_output --partial "deps"
    assert_output --partial "migrate"
    assert_output --partial "cron"
    assert_output --partial "aliases"
    assert_output --partial "deploy"
}

@test "install.sh help mentions --profile and --yes and --save-profile" {
    run "$BIN_DIR/install.sh" help
    assert_success
    assert_output --partial "--profile"
    assert_output --partial "--yes"
    assert_output --partial "--save-profile"
}

@test "install.sh rejects unknown subcommand" {
    run "$BIN_DIR/install.sh" foobar
    assert_failure
    assert_output --partial "Unknown step"
}

@test "_aliases_read_existing_prefix returns prefix from # Prefix: header" {
    local BIN_DIR_REAL="$BIN_DIR"
    run bash -c '
        export HOME="$(mktemp -d)"
        TEST_BIN_DIR="$(mktemp -d)"
        cat > "$TEST_BIN_DIR/aliases.sh" <<EOF
# Prefix: maint
alias maint-check-health='\''cd "/tmp" && true'\''
EOF
        # Sourcing aliases.sh re-derives BIN_DIR/PROJECT_DIR via lib/paths.sh,
        # so we override ALIASES_FILE after the source.
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$TEST_BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output "maint"
}

@test "_aliases_read_existing_prefix falls back to alias-name parsing when header missing" {
    local BIN_DIR_REAL="$BIN_DIR"
    run bash -c '
        export HOME="$(mktemp -d)"
        TEST_BIN_DIR="$(mktemp -d)"
        cat > "$TEST_BIN_DIR/aliases.sh" <<EOF
# No prefix header here
alias custom-check-health='\''cd "/tmp" && true'\''
alias custom-run-check='\''cd "/tmp" && true'\''
EOF
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$TEST_BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output "custom"
}

@test "_aliases_read_existing_prefix returns empty when both methods fail" {
    local BIN_DIR_REAL="$BIN_DIR"
    run bash -c '
        export HOME="$(mktemp -d)"
        TEST_BIN_DIR="$(mktemp -d)"
        cat > "$TEST_BIN_DIR/aliases.sh" <<EOF
# Some other file with no header and no -check-health alias
alias something-else='\''cd "/tmp" && true'\''
EOF
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$TEST_BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output ""
}

@test "install.sh aliases --no-profile regenerates aliases without modifying profile" {
    local BIN_DIR_REAL="$BIN_DIR"
    run bash -c '
        export HOME="$(mktemp -d)"
        export SHELL=/bin/bash
        : > "$HOME/.bashrc"
        # Use a separate, isolated BIN_DIR so we do not touch the repo aliases.sh.
        export TEST_BIN="$(mktemp -d)"
        export BIN_DIR="$TEST_BIN"
        export PROJECT_DIR="$(dirname "$TEST_BIN")"
        mkdir -p "$TEST_BIN/install" "$TEST_BIN/lib"
        cp -r "'"$BIN_DIR_REAL/lib"'/." "$TEST_BIN/lib/"
        cp "'"$BIN_DIR_REAL/install/aliases.sh"'" "$TEST_BIN/install/aliases.sh"
        bash "$TEST_BIN/install/aliases.sh" --prefix sm --no-profile >/dev/null 2>&1
        # Aliases file was written...
        [ -f "$TEST_BIN/aliases.sh" ]
        # ...but the profile was NOT touched.
        [ ! -s "$HOME/.bashrc" ]
    '
    assert_success
}

@test "install.sh aliases --prefix without --no-profile DOES modify profile" {
    local BIN_DIR_REAL="$BIN_DIR"
    run bash -c '
        export HOME="$(mktemp -d)"
        export SHELL=/bin/bash
        : > "$HOME/.bashrc"
        export TEST_BIN="$(mktemp -d)"
        export BIN_DIR="$TEST_BIN"
        export PROJECT_DIR="$(dirname "$TEST_BIN")"
        mkdir -p "$TEST_BIN/install" "$TEST_BIN/lib"
        cp -r "'"$BIN_DIR_REAL/lib"'/." "$TEST_BIN/lib/"
        cp "'"$BIN_DIR_REAL/install/aliases.sh"'" "$TEST_BIN/install/aliases.sh"
        bash "$TEST_BIN/install/aliases.sh" --prefix sm >/dev/null 2>&1
        [ -f "$TEST_BIN/aliases.sh" ]
        # Source line WAS added to the (otherwise empty) profile.
        grep -qF "server-maintanence aliases" "$HOME/.bashrc"
    '
    assert_success
}

@test "install/cron.sh schedules no second self-check job" {
    # run_pipeline --checks-only above is already this machine's self-monitoring:
    # it enters the pipeline at CHECK and routes the matched lane synchronously.
    # A push_to_hub --local job would duplicate that alert on the same schedule
    # and leave a PENDING run that a cron-only install never drains.
    run grep -q "push_to_hub --local" "$BIN_DIR/install/cron.sh"
    assert_failure
}

# ---------------------------------------------------------------------------
# env.sh owns the baseline .env, and this machine's identity is part of it:
# INSTANCE_ID keys its Node row and its check:{instance_id}:{checker} alert
# fingerprints, so it cannot live behind the cluster step's y/N gate.
# ---------------------------------------------------------------------------

# _run_env PROJECT ANSWERS...
#
# Drive env.sh non-interactively against a throwaway PROJECT_DIR/.env.
# The alarm turns a prompt that outlives its answers into a failure rather
# than a hung run.
_run_env() {
    local proj="$1"
    shift
    printf '%s\n' "$@" > "$proj/answers"
    perl -e 'alarm 10; exec @ARGV' \
        env PROJECT_DIR="$proj" \
        bash "$BIN_DIR/install/env.sh" \
        < "$proj/answers" > "$proj/install.log" 2>&1
}

@test "env.sh writes a non-empty INSTANCE_ID on a standalone install" {
    local proj
    proj="$(mktemp -d)"
    : > "$proj/.env"

    # dev, bare, default debug, default hosts, auto-generate secret key
    run _run_env "$proj" "1" "1" "" "" "y"
    assert_success

    run grep -E '^INSTANCE_ID=.+' "$proj/.env"
    assert_success
}

@test "env.sh default INSTANCE_ID is not a bare hostname" {
    local proj
    proj="$(mktemp -d)"
    : > "$proj/.env"

    _run_env "$proj" "1" "1" "" "" "y"

    local written
    written="$(grep -E '^INSTANCE_ID=' "$proj/.env" | tail -1 | cut -d= -f2-)"
    refute [ "$written" = "$(hostname)" ]
    [[ "$written" =~ -[0-9a-f]+$ ]]
}

@test "env.sh re-run keeps an existing INSTANCE_ID unchanged" {
    local proj
    proj="$(mktemp -d)"
    printf 'INSTANCE_ID=web-03-a1b2c3d4\n' > "$proj/.env"

    _run_env "$proj" "1" "1" "" "" "y"

    run grep -c '^INSTANCE_ID=' "$proj/.env"
    assert_output "1"
    run grep '^INSTANCE_ID=' "$proj/.env"
    assert_output "INSTANCE_ID=web-03-a1b2c3d4"
}
