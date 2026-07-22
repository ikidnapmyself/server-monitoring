#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
}

@test "set_production.sh passes syntax check" {
    run bash -n "$BIN_DIR/set_production.sh"
    assert_success
}

@test "set_production.sh exists and is executable" {
    [ -x "$BIN_DIR/set_production.sh" ]
}

@test "set_production.sh --help shows usage" {
    run "$BIN_DIR/set_production.sh" --help
    assert_success
    assert_output --partial "Usage"
    assert_output --partial "Convert a dev environment to production"
    assert_output --partial "DJANGO_ENV=prod"
    assert_output --partial "DJANGO_DEBUG=0"
}

# Production needs gunicorn (the `prod` extra); a plain `uv sync` would strip it.
# Stub `uv` on PATH to record args, and pre-fill .env so no prompts are hit.
@test "set_production.sh syncs with the prod extra" {
    local tmp stub_bin
    tmp="$(mktemp -d)"
    printf 'DJANGO_ENV=dev\nDJANGO_DEBUG=1\nDJANGO_SECRET_KEY=already-set\nDJANGO_ALLOWED_HOSTS=example.com\n' \
        > "$tmp/.env"
    stub_bin="$(mktemp -d)"
    printf '#!/usr/bin/env bash\necho "uv $*"\n' > "$stub_bin/uv"
    chmod +x "$stub_bin/uv"

    export PROJECT_DIR="$tmp"
    PATH="$stub_bin:$PATH" run "$BIN_DIR/set_production.sh"
    assert_success
    assert_output --partial "uv sync --extra prod"
}
