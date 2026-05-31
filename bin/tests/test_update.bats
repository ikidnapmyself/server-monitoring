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

@test "update lib dry-run sync aliases logs regeneration step" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        temp_bin="$(mktemp -d)"
        BIN_DIR="$temp_bin"
        PROJECT_DIR="$(dirname "$temp_bin")"
        cat > "$BIN_DIR/aliases.sh" <<EOF
# Prefix: sm
alias sm-check-health='\''cd "/tmp" && true'\''
EOF
        _up_dry_run=true
        _up_json_mode=false
        _up_sync_aliases
    '
    assert_success
    assert_output --partial "Dry-run: would run install.sh aliases --no-profile"
}

@test "_up_sync_aliases skips when aliases.sh does not exist" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        temp_bin="$(mktemp -d)"
        BIN_DIR="$temp_bin"
        PROJECT_DIR="$(dirname "$temp_bin")"
        _up_dry_run=false
        _up_json_mode=false
        _up_sync_aliases
    '
    assert_success
    assert_output --partial "Aliases not configured"
}

@test "_up_sync_aliases logs WARN and returns 0 when install.sh fails" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        temp_bin="$(mktemp -d)"
        BIN_DIR="$temp_bin"
        PROJECT_DIR="$(dirname "$temp_bin")"

        # Provide a fake aliases.sh so the early-return is skipped.
        cat > "$BIN_DIR/aliases.sh" <<EOF
# Prefix: sm
alias sm-check-health='\''cd "/tmp" && true'\''
EOF

        # Fake install.sh that exits 1.
        cat > "$BIN_DIR/install.sh" <<EOF
#!/usr/bin/env bash
exit 1
EOF
        chmod +x "$BIN_DIR/install.sh"

        _up_dry_run=false
        _up_json_mode=false
        _up_sync_aliases
    '
    assert_success
    assert_output --partial "Alias regeneration failed"
}
