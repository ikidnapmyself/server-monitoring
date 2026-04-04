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
WEBHOOK_SECRET_CLUSTER=topsecret
CELERY_BROKER_URL=redis://localhost:6379/0
ENVEOF

    export PROJECT_DIR="$TEST_TMPDIR"
    profile_save "$TEST_TMPDIR/.install-profile" "test-profile"

    grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.install-profile"
    grep -q "DEPLOY_METHOD=bare" "$TEST_TMPDIR/.install-profile"
    grep -q "CELERY_BROKER_URL=redis://localhost:6379/0" "$TEST_TMPDIR/.install-profile"
    ! grep -q "DJANGO_SECRET_KEY" "$TEST_TMPDIR/.install-profile"
    ! grep -q "WEBHOOK_SECRET_CLUSTER" "$TEST_TMPDIR/.install-profile"
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