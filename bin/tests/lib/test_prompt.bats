#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/prompt.sh"
    TEST_TMPDIR="$(mktemp -d)"
}

teardown() {
    rm -rf "$TEST_TMPDIR"
}

@test "prompt.sh passes syntax check" {
    run bash -n "$LIB_DIR/prompt.sh"
    assert_success
}

# --- prompt_with_default ---

@test "prompt_with_default returns existing value on empty input" {
    echo "MY_KEY=existing_val" > "$TEST_TMPDIR/.env"
    result="$(echo "" | prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value")"
    [ "$result" = "existing_val" ]
}

@test "prompt_with_default uses fallback when key missing" {
    touch "$TEST_TMPDIR/.env"
    result="$(echo "" | prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value" "fallback_val")"
    [ "$result" = "fallback_val" ]
}

@test "prompt_with_default uses user input over existing value" {
    echo "MY_KEY=existing_val" > "$TEST_TMPDIR/.env"
    result="$(echo "user_input" | prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value")"
    [ "$result" = "user_input" ]
}

@test "prompt_with_default uses fallback when key has empty value" {
    echo "MY_KEY=" > "$TEST_TMPDIR/.env"
    result="$(echo "" | prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value" "fb")"
    [ "$result" = "fb" ]
}

@test "prompt_with_default uses user input when no default and no fallback" {
    touch "$TEST_TMPDIR/.env"
    result="$(echo "typed" | prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value")"
    [ "$result" = "typed" ]
}

# --- prompt_choice ---

@test "prompt_choice returns existing value on empty input" {
    echo "ROLE=worker" > "$TEST_TMPDIR/.env"
    result="$(echo "" | prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node")"
    [ "$result" = "worker" ]
}

@test "prompt_choice accepts numeric input" {
    touch "$TEST_TMPDIR/.env"
    result="$(echo "2" | prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node")"
    [ "$result" = "worker" ]
}

@test "prompt_choice accepts text matching option value" {
    touch "$TEST_TMPDIR/.env"
    result="$(echo "master" | prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node")"
    [ "$result" = "master" ]
}

@test "prompt_choice rejects invalid input then accepts valid" {
    touch "$TEST_TMPDIR/.env"
    result="$(printf "99\n1\n" | prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node")"
    [ "$result" = "master" ]
}

# --- prompt_yes_no ---

@test "prompt_yes_no returns 0 for y" {
    run bash -c 'source "'"$LIB_DIR"'/prompt.sh"; echo "y" | prompt_yes_no "Continue?"'
    assert_success
}

@test "prompt_yes_no returns 1 for n" {
    run bash -c 'source "'"$LIB_DIR/prompt.sh"'"; echo "n" | prompt_yes_no "Continue?"'
    assert_failure
}

@test "prompt_yes_no defaults to no on empty input" {
    run bash -c 'source "'"$LIB_DIR/prompt.sh"'"; echo "" | prompt_yes_no "Continue?"'
    assert_failure
}

@test "prompt_yes_no defaults to yes when default_y specified" {
    run bash -c 'source "'"$LIB_DIR"'/prompt.sh"; echo "" | prompt_yes_no "Continue?" "default_y"'
    assert_success
}

# --- PROMPT_MASK ---

@test "prompt_with_default masks display but returns actual value when PROMPT_MASK=1" {
    echo "SECRET_KEY=hunter2" > "$TEST_TMPDIR/.env"
    local tmpstderr
    tmpstderr="$(mktemp)"
    run bash -c '
        source "'"$LIB_DIR"'/prompt.sh"
        export PROMPT_MASK=1
        echo "" | prompt_with_default "'"$TEST_TMPDIR"'/.env" "SECRET_KEY" "Enter secret" 2>"'"$tmpstderr"'"
    '
    assert_success
    assert_output "hunter2"

    stderr_content="$(cat "$tmpstderr")"
    [[ "$stderr_content" == *"••••••••"* ]]
    [[ "$stderr_content" != *"hunter2"* ]]
    rm -f "$tmpstderr"
}

# --- prompt_choice edge cases ---

@test "prompt_choice retries on empty input when no current value" {
    touch "$TEST_TMPDIR/.env"
    result="$(printf '\n2\n' | prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node" 2>/dev/null)"
    [ "$result" = "worker" ]
}

# --- prompt_yes_no edge cases ---

@test "prompt_yes_no retries on invalid input then accepts valid" {
    run bash -c 'source "'"$LIB_DIR"'/prompt.sh"; printf "m\ny\n" | prompt_yes_no "Continue?"'
    assert_success
}

# --- INSTALL_AUTO_ACCEPT ---

@test "prompt_with_default auto-accepts existing value when INSTALL_AUTO_ACCEPT=1" {
    echo "MY_KEY=auto_val" > "$TEST_TMPDIR/.env"
    result="$(INSTALL_AUTO_ACCEPT=1 prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value")"
    [ "$result" = "auto_val" ]
}

@test "prompt_with_default auto-accepts fallback when INSTALL_AUTO_ACCEPT=1 and key missing" {
    touch "$TEST_TMPDIR/.env"
    result="$(INSTALL_AUTO_ACCEPT=1 prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value" "fb")"
    [ "$result" = "fb" ]
}

@test "prompt_with_default still prompts when INSTALL_AUTO_ACCEPT=1 but no default exists" {
    touch "$TEST_TMPDIR/.env"
    result="$(echo "typed" | INSTALL_AUTO_ACCEPT=1 prompt_with_default "$TEST_TMPDIR/.env" "MY_KEY" "Enter value")"
    [ "$result" = "typed" ]
}

@test "prompt_choice auto-accepts current value when INSTALL_AUTO_ACCEPT=1" {
    echo "ROLE=worker" > "$TEST_TMPDIR/.env"
    result="$(INSTALL_AUTO_ACCEPT=1 prompt_choice "$TEST_TMPDIR/.env" "ROLE" "Select role" "master:Master node" "worker:Worker node")"
    [ "$result" = "worker" ]
}

@test "prompt_yes_no auto-accepts default_y when INSTALL_AUTO_ACCEPT=1" {
    run bash -c 'source "'"$LIB_DIR"'/prompt.sh"; INSTALL_AUTO_ACCEPT=1 prompt_yes_no "Continue?" "default_y"'
    assert_success
}

@test "prompt_yes_no auto-accepts default_n when INSTALL_AUTO_ACCEPT=1" {
    run bash -c 'source "'"$LIB_DIR"'/prompt.sh"; INSTALL_AUTO_ACCEPT=1 prompt_yes_no "Continue?"'
    assert_failure
}