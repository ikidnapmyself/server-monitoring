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

@test "install/celery.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/celery.sh"
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
    assert_output --partial "celery"
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
