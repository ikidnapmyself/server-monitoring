#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/tuin_vendor.sh"
}

@test "ensure_tuin is a no-op when tuin.sh already exists" {
    # tuin.sh is vendored in the repo, so TUIN_LOCAL exists.
    [ -f "$TUIN_LOCAL" ]
    # Stub curl so that any network attempt would fail loudly.
    curl() { echo "curl should not be called" >&2; return 99; }
    export -f curl
    run ensure_tuin
    assert_success
}

@test "vendor_tuin fails with curl-required message when curl is missing" {
    # Run in a clean subshell with an empty PATH so 'command -v curl' fails.
    run bash -c 'source "'"$LIB_DIR/tuin_vendor.sh"'" && PATH="" vendor_tuin'
    assert_failure
    assert_output --partial "curl is required to vendor tuin"
}

@test "TUIN_URL honors a custom TUIN_VERSION" {
    # Fresh load in a subshell so the source-guard and vars are reset.
    run bash -c 'TUIN_VERSION=v9.9.9 source "'"$LIB_DIR/tuin_vendor.sh"'" && echo "$TUIN_URL"'
    assert_success
    assert_output --partial "v9.9.9"
}