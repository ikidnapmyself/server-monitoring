#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/profile.sh"
    TEST_TMPDIR="$(mktemp -d)"
}

teardown() {
    rm -rf "$TEST_TMPDIR"
}

@test "profile.sh passes syntax check" {
    run bash -n "$LIB_DIR/profile.sh"
    assert_success
}

@test "profile_save writes non-sensitive keys" {
    cat > "$TEST_TMPDIR/.env" <<'ENVEOF'
DJANGO_ENV=prod
DEPLOY_METHOD=bare
DJANGO_SECRET_KEY=supersecret
DJANGO_DEBUG=0
HUB_API_KEY=topsecret
CELERY_BROKER_URL=redis://localhost:6379/0
ENVEOF

    export PROJECT_DIR="$TEST_TMPDIR"
    profile_save "$TEST_TMPDIR/.install-profile" "test-profile"

    grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.install-profile"
    grep -q "DEPLOY_METHOD=bare" "$TEST_TMPDIR/.install-profile"
    grep -q "CELERY_BROKER_URL=redis://localhost:6379/0" "$TEST_TMPDIR/.install-profile"
    ! grep -q "DJANGO_SECRET_KEY" "$TEST_TMPDIR/.install-profile"
    ! grep -q "HUB_API_KEY" "$TEST_TMPDIR/.install-profile"
}

@test "profile_save writes metadata header" {
    cat > "$TEST_TMPDIR/.env" <<'ENVEOF'
DJANGO_ENV=dev
ENVEOF

    export PROJECT_DIR="$TEST_TMPDIR"
    profile_save "$TEST_TMPDIR/.install-profile" "my-profile"

    grep -q "# name: my-profile" "$TEST_TMPDIR/.install-profile"
    grep -q "# created:" "$TEST_TMPDIR/.install-profile"
    grep -q "# hostname:" "$TEST_TMPDIR/.install-profile"
    grep -q "# installer_version:" "$TEST_TMPDIR/.install-profile"
}

@test "profile_save captures installer state variables" {
    cat > "$TEST_TMPDIR/.env" <<'ENVEOF'
DJANGO_ENV=dev
ENVEOF

    export PROJECT_DIR="$TEST_TMPDIR"
    export CRON_SCHEDULE="*/5 * * * *"
    export CRON_AUTO_UPDATE=1
    export ALIAS_PREFIX=sm
    profile_save "$TEST_TMPDIR/.install-profile" "test"

    grep -q "CRON_SCHEDULE=" "$TEST_TMPDIR/.install-profile"
    grep -q "CRON_AUTO_UPDATE=1" "$TEST_TMPDIR/.install-profile"
    grep -q "ALIAS_PREFIX=sm" "$TEST_TMPDIR/.install-profile"

    unset CRON_SCHEDULE CRON_AUTO_UPDATE ALIAS_PREFIX
}

@test "profile_load writes values to .env" {
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
# server-maintanence install profile
# name: test
DJANGO_ENV=prod
DEPLOY_METHOD=docker
PROFEOF

    touch "$TEST_TMPDIR/.env"
    export PROJECT_DIR="$TEST_TMPDIR"
    profile_load "$TEST_TMPDIR/.install-profile"

    grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.env"
    grep -q "DEPLOY_METHOD=docker" "$TEST_TMPDIR/.env"
}

@test "profile_load skips comments and blank lines" {
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
# server-maintanence install profile
# name: test

DJANGO_ENV=prod

# Celery
CELERY_TASK_ALWAYS_EAGER=0
PROFEOF

    touch "$TEST_TMPDIR/.env"
    export PROJECT_DIR="$TEST_TMPDIR"
    profile_load "$TEST_TMPDIR/.install-profile"

    grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.env"
    grep -q "CELERY_TASK_ALWAYS_EAGER=0" "$TEST_TMPDIR/.env"
    ! grep -q "^# name:" "$TEST_TMPDIR/.env"
}

@test "profile_load warns and skips sensitive keys" {
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
DJANGO_ENV=prod
DJANGO_SECRET_KEY=shouldnotload
PROFEOF

    touch "$TEST_TMPDIR/.env"
    export PROJECT_DIR="$TEST_TMPDIR"
    run bash -c 'source "'"$LIB_DIR"'/profile.sh"; export PROJECT_DIR="'"$TEST_TMPDIR"'"; profile_load "'"$TEST_TMPDIR"'/.install-profile"'
    assert_success
    assert_output --partial "WARN"
    ! grep -q "DJANGO_SECRET_KEY" "$TEST_TMPDIR/.env"
}

@test "profile_metadata reads metadata values" {
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
# server-maintanence install profile
# name: my-fleet-profile
# created: 2026-04-04T14:30:00
# hostname: web-01
DJANGO_ENV=prod
PROFEOF

    result="$(profile_metadata "$TEST_TMPDIR/.install-profile" "name")"
    [ "$result" = "my-fleet-profile" ]
    result="$(profile_metadata "$TEST_TMPDIR/.install-profile" "hostname")"
    [ "$result" = "web-01" ]
}

# ---------------------------------------------------------------------------
# INSTANCE_ID is machine-unique, not portable: it keys this machine's Node row
# and its alert fingerprints, so a profile must never carry one to a second
# machine — that would silently merge the two into one identity.
# ---------------------------------------------------------------------------

@test "profile_save omits the machine-unique INSTANCE_ID" {
    cat > "$TEST_TMPDIR/.env" <<'ENVEOF'
DJANGO_ENV=prod
INSTANCE_ID=web-03-a1b2c3d4
HUB_URL=https://hub.example.com
ENVEOF

    export PROJECT_DIR="$TEST_TMPDIR"
    profile_save "$TEST_TMPDIR/.install-profile" "test-profile"

    run grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.install-profile"
    assert_success
    run grep -q "HUB_URL=https://hub.example.com" "$TEST_TMPDIR/.install-profile"
    assert_success
    # `run` + assert_failure, not a bare `! grep`: set -e ignores a negated
    # pipeline, so a bare `!` assertion can never fail the test.
    run grep -q "INSTANCE_ID" "$TEST_TMPDIR/.install-profile"
    assert_failure
}

@test "profile_load gives the restoring machine its own INSTANCE_ID" {
    # An older or hand-edited profile may still carry the source machine's id.
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
# server-maintanence install profile
# name: test
DJANGO_ENV=prod
INSTANCE_ID=source-machine-a1b2c3d4
PROFEOF

    touch "$TEST_TMPDIR/.env"
    export PROJECT_DIR="$TEST_TMPDIR"
    profile_load "$TEST_TMPDIR/.install-profile"

    run grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.env"
    assert_success
    run grep -q "source-machine-a1b2c3d4" "$TEST_TMPDIR/.env"
    assert_failure
    # A restore never leaves the identity empty.
    run grep -qE "^INSTANCE_ID=.+" "$TEST_TMPDIR/.env"
    assert_success
}

@test "profile_load keeps an INSTANCE_ID this machine already has" {
    cat > "$TEST_TMPDIR/.install-profile" <<'PROFEOF'
# server-maintanence install profile
DJANGO_ENV=prod
PROFEOF

    printf 'INSTANCE_ID=web-03-a1b2c3d4\n' > "$TEST_TMPDIR/.env"
    export PROJECT_DIR="$TEST_TMPDIR"
    profile_load "$TEST_TMPDIR/.install-profile"

    run grep -c "^INSTANCE_ID=" "$TEST_TMPDIR/.env"
    assert_output "1"
    run grep "^INSTANCE_ID=" "$TEST_TMPDIR/.env"
    assert_output "INSTANCE_ID=web-03-a1b2c3d4"
}
