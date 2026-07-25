#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/paths.sh"
}

@test "BIN_DIR is set and points to bin/" {
    [ -n "$BIN_DIR" ]
    [[ "$BIN_DIR" == */bin ]]
    [ -d "$BIN_DIR" ]
}

@test "PROJECT_DIR is set and is parent of BIN_DIR" {
    [ -n "$PROJECT_DIR" ]
    [ -d "$PROJECT_DIR" ]
    [ "$(dirname "$BIN_DIR")" = "$PROJECT_DIR" ]
}

@test "PROJECT_DIR contains pyproject.toml" {
    [ -f "$PROJECT_DIR/pyproject.toml" ]
}

@test "resolve_project_dir returns correct path from nested dir" {
    run bash -c 'cd /tmp && source "'"$LIB_DIR/paths.sh"'" && echo "$PROJECT_DIR"'
    assert_success
    assert_output "$PROJECT_DIR"
}

@test "caller-provided BIN_DIR is preserved" {
    local custom_bin="$BATS_TEST_TMPDIR/custom/bin"
    mkdir -p "$custom_bin"

    run env -u PROJECT_DIR -u LOG_DIR BIN_DIR="$custom_bin" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && printf "%s\n%s\n" "$BIN_DIR" "$PROJECT_DIR"'
    assert_success
    assert_line --index 0 "$custom_bin"
    assert_line --index 1 "$(dirname "$custom_bin")"
}

@test "caller-provided PROJECT_DIR is preserved (independent of BIN_DIR)" {
    local custom_bin="$BATS_TEST_TMPDIR/custom/bin"
    local custom_proj="$BATS_TEST_TMPDIR/somewhere/else"
    mkdir -p "$custom_bin" "$custom_proj"

    run env BIN_DIR="$custom_bin" PROJECT_DIR="$custom_proj" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && printf "%s\n%s\n" "$BIN_DIR" "$PROJECT_DIR"'
    assert_success
    assert_line --index 0 "$custom_bin"
    assert_line --index 1 "$custom_proj"
}

@test "caller-provided LOG_DIR is preserved" {
    local custom_log="$BATS_TEST_TMPDIR/custom/logs"
    mkdir -p "$custom_log"

    run env LOG_DIR="$custom_log" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && echo "$LOG_DIR"'
    assert_success
    assert_output "$custom_log"
}

# --- uv resolution (UV_BIN) --------------------------------------------------

@test "resolve_uv honors an explicit absolute UV_BIN" {
    local fake="$BATS_TEST_TMPDIR/bin/uv"
    mkdir -p "$(dirname "$fake")"
    printf '#!/usr/bin/env bash\ntrue\n' > "$fake"
    chmod 755 "$fake"

    run env UV_BIN="$fake" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && echo "$UV_BIN"'
    assert_success
    assert_output "$fake"
}

@test "resolve_uv rejects a relative UV_BIN (never a PATH lookup)" {
    run env UV_BIN="uv" bash -c \
        'source "'"$LIB_DIR/paths.sh"'"'
    assert_failure
    assert_output --partial "must be an absolute path"
}

@test "resolve_uv rejects a group/world-writable uv (tamper guard)" {
    local fake="$BATS_TEST_TMPDIR/writable/uv"
    mkdir -p "$(dirname "$fake")"
    printf '#!/usr/bin/env bash\ntrue\n' > "$fake"
    chmod 777 "$fake"

    run env UV_BIN="$fake" bash -c \
        'source "'"$LIB_DIR/paths.sh"'"'
    assert_failure
    assert_output --partial "group/world-writable"
}

@test "resolve_uv probes the current user's ~/.local/bin, not PATH" {
    local home="$BATS_TEST_TMPDIR/home"
    mkdir -p "$home/.local/bin"
    printf '#!/usr/bin/env bash\ntrue\n' > "$home/.local/bin/uv"
    chmod 755 "$home/.local/bin/uv"

    run env -u UV_BIN HOME="$home" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && echo "$UV_BIN"'
    assert_success
    assert_output "$home/.local/bin/uv"
}

@test "resolve_uv leaves UV_BIN empty (non-fatal) when uv is absent" {
    local home="$BATS_TEST_TMPDIR/empty-home"
    mkdir -p "$home"

    run env -u UV_BIN HOME="$home" bash -c \
        'source "'"$LIB_DIR/paths.sh"'" && echo "rc=$?" && echo "uv=[$UV_BIN]"'
    assert_success
    assert_output --partial "uv=[]"
}
