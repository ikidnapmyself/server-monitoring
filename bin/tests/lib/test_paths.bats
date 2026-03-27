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