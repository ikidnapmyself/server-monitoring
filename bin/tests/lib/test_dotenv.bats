#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/dotenv.sh"
    TEST_TMPDIR="$(mktemp -d)"
}

teardown() {
    rm -rf "$TEST_TMPDIR"
}

@test "dotenv_has_key finds existing key" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    run dotenv_has_key "$TEST_TMPDIR/.env" "FOO"
    assert_success
}

@test "dotenv_has_key finds key with spaces around =" {
    echo "  FOO = bar" > "$TEST_TMPDIR/.env"
    run dotenv_has_key "$TEST_TMPDIR/.env" "FOO"
    assert_success
}

@test "dotenv_has_key returns failure for missing key" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    run dotenv_has_key "$TEST_TMPDIR/.env" "BAZ"
    assert_failure
}

@test "dotenv_has_key returns failure for empty file" {
    touch "$TEST_TMPDIR/.env"
    run dotenv_has_key "$TEST_TMPDIR/.env" "FOO"
    assert_failure
}

@test "dotenv_set_if_missing appends key when missing" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    dotenv_set_if_missing "$TEST_TMPDIR/.env" "BAZ" "qux"
    run grep -c "BAZ=qux" "$TEST_TMPDIR/.env"
    assert_output "1"
}

@test "dotenv_set_if_missing does not overwrite existing key" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    dotenv_set_if_missing "$TEST_TMPDIR/.env" "FOO" "new_value"
    run grep "FOO" "$TEST_TMPDIR/.env"
    assert_output "FOO=bar"
}

@test "dotenv_set_if_missing works on empty file" {
    touch "$TEST_TMPDIR/.env"
    dotenv_set_if_missing "$TEST_TMPDIR/.env" "KEY" "value"
    run cat "$TEST_TMPDIR/.env"
    assert_output "KEY=value"
}

@test "dotenv_ensure_file copies from sample when .env missing" {
    echo "SAMPLE_KEY=sample" > "$TEST_TMPDIR/.env.sample"
    PROJECT_DIR="$TEST_TMPDIR"
    dotenv_ensure_file
    [ -f "$TEST_TMPDIR/.env" ]
    run cat "$TEST_TMPDIR/.env"
    assert_output "SAMPLE_KEY=sample"
}

@test "dotenv_ensure_file does nothing when .env exists" {
    echo "EXISTING=true" > "$TEST_TMPDIR/.env"
    echo "SAMPLE_KEY=sample" > "$TEST_TMPDIR/.env.sample"
    PROJECT_DIR="$TEST_TMPDIR"
    dotenv_ensure_file
    run cat "$TEST_TMPDIR/.env"
    assert_output "EXISTING=true"
}

@test "dotenv_ensure_file creates empty .env when no sample exists" {
    PROJECT_DIR="$TEST_TMPDIR"
    dotenv_ensure_file
    [ -f "$TEST_TMPDIR/.env" ]
    run cat "$TEST_TMPDIR/.env"
    assert_output ""
}