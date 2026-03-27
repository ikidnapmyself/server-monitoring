#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/docker.sh"
}

@test "parse_service_state extracts state from JSON array" {
    local json='[{"Service":"web","State":"running"},{"Service":"celery","State":"exited"}]'
    run parse_service_state "web" <<< "$json"
    assert_success
    assert_output "running"
}

@test "parse_service_state extracts state from NDJSON" {
    local json=$'{"Service":"web","State":"running"}\n{"Service":"celery","State":"exited"}'
    run parse_service_state "web" <<< "$json"
    assert_success
    assert_output "running"
}

@test "parse_service_state returns empty for missing service" {
    local json='[{"Service":"web","State":"running"}]'
    run parse_service_state "celery" <<< "$json"
    assert_output ""
}

@test "parse_service_state handles empty input" {
    run parse_service_state "web" <<< ""
    assert_output ""
}

@test "docker_preflight fails without docker" {
    PATH="/usr/bin:/bin"
    run docker_preflight
    assert_failure
}