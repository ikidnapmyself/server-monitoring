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

@test "update lib reads alias prefix from generated aliases file" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        aliases_file="$(mktemp)"
        cat > "$aliases_file" <<EOF
# Prefix: maint
alias maint-check-health='\''cd "/tmp" && true'\''
EOF
        prefix="$(_up_aliases_read_prefix "$aliases_file")"
        [[ "$prefix" == "maint" ]]
    '
    assert_success
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
    assert_output --partial "Dry-run: would run install.sh aliases --prefix sm"
}
