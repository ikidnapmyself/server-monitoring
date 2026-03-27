#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/colors.sh"
}

@test "RED is defined and non-empty" {
    [ -n "$RED" ]
}

@test "GREEN is defined and non-empty" {
    [ -n "$GREEN" ]
}

@test "YELLOW is defined and non-empty" {
    [ -n "$YELLOW" ]
}

@test "BLUE is defined and non-empty" {
    [ -n "$BLUE" ]
}

@test "CYAN is defined and non-empty" {
    [ -n "$CYAN" ]
}

@test "BOLD is defined and non-empty" {
    [ -n "$BOLD" ]
}

@test "NC is defined and non-empty" {
    [ -n "$NC" ]
}

@test "colors contain ANSI escape sequences" {
    [[ "$RED" == *$'\033['* ]]
    [[ "$GREEN" == *$'\033['* ]]
    [[ "$NC" == *$'\033['* ]]
}