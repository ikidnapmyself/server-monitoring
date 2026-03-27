#!/usr/bin/env bash

_common_setup() {
    TEST_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
    TESTS_ROOT="$(cd "$TEST_DIR" && while [ ! -d test_helper ]; do cd ..; done; pwd)"
    BIN_DIR="$(dirname "$TESTS_ROOT")"
    PROJECT_DIR="$(dirname "$BIN_DIR")"
    LIB_DIR="$BIN_DIR/lib"

    load "${TESTS_ROOT}/test_helper/bats-support/load"
    load "${TESTS_ROOT}/test_helper/bats-assert/load"
}