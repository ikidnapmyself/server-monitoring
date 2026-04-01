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

@test "dotenv_has_value finds key with non-empty value" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    run dotenv_has_value "$TEST_TMPDIR/.env" "FOO"
    assert_success
}

@test "dotenv_has_value returns failure for key with empty value" {
    echo "FOO=" > "$TEST_TMPDIR/.env"
    run dotenv_has_value "$TEST_TMPDIR/.env" "FOO"
    assert_failure
}

@test "dotenv_has_value returns failure for missing key" {
    echo "FOO=bar" > "$TEST_TMPDIR/.env"
    run dotenv_has_value "$TEST_TMPDIR/.env" "BAZ"
    assert_failure
}

@test "dotenv_set overwrites existing empty value" {
    echo "SECRET_KEY=" > "$TEST_TMPDIR/.env"
    dotenv_set "$TEST_TMPDIR/.env" "SECRET_KEY" "abc123"
    run grep "SECRET_KEY" "$TEST_TMPDIR/.env"
    assert_output "SECRET_KEY=abc123"
}

@test "dotenv_set overwrites existing non-empty value" {
    echo "SECRET_KEY=old" > "$TEST_TMPDIR/.env"
    dotenv_set "$TEST_TMPDIR/.env" "SECRET_KEY" "new"
    run grep "SECRET_KEY" "$TEST_TMPDIR/.env"
    assert_output "SECRET_KEY=new"
}

@test "dotenv_set appends when key is missing" {
    echo "OTHER=val" > "$TEST_TMPDIR/.env"
    dotenv_set "$TEST_TMPDIR/.env" "NEW_KEY" "new_val"
    run grep "NEW_KEY" "$TEST_TMPDIR/.env"
    assert_output "NEW_KEY=new_val"
}

@test "dotenv_set preserves other keys" {
    printf "AAA=111\nBBB=\nCCC=333\n" > "$TEST_TMPDIR/.env"
    dotenv_set "$TEST_TMPDIR/.env" "BBB" "222"
    run cat "$TEST_TMPDIR/.env"
    assert_line --index 0 "AAA=111"
    assert_line --index 1 "BBB=222"
    assert_line --index 2 "CCC=333"
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