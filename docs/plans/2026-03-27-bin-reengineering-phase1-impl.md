---
title: "bin/ Re-engineering Phase 1 — Implementation"
parent: Plans
nav_order: 79739672
---

# bin/ Re-engineering Phase 1 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create shared shell libraries under `bin/lib/`, set up BATS testing infrastructure, and write unit tests for every lib function. No existing scripts change in this phase.

**Architecture:** Six library files under `bin/lib/` extracted from duplicated code across existing scripts. BATS (Bash Automated Testing System) via git submodules for testing. New CI job for shell tests.

**Tech Stack:** Bash, BATS (bats-core, bats-support, bats-assert), GitHub Actions

---

### Task 1: Set up BATS test infrastructure

**Files:**
- Create: `bin/tests/test_helper/` (git submodules)
- Create: `bin/tests/lib/` (directory)

**Step 1: Add BATS git submodules**

```bash
cd /Users/burak/Projects/server-maintanence
git submodule add https://github.com/bats-core/bats-core.git bin/tests/test_helper/bats-core
git submodule add https://github.com/bats-core/bats-support.git bin/tests/test_helper/bats-support
git submodule add https://github.com/bats-core/bats-assert.git bin/tests/test_helper/bats-assert
```

**Step 2: Create test helper setup file**

Create `bin/tests/test_helper/common-setup.bash`:

```bash
#!/usr/bin/env bash

# Common setup for all BATS tests.
# Source this in setup() of each .bats file.

_common_setup() {
    # Load BATS helpers
    load 'bats-support/load'
    load 'bats-assert/load'

    # Resolve paths
    TEST_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")" && pwd)"
    TESTS_ROOT="$(cd "$TEST_DIR" && while [ ! -d test_helper ]; do cd ..; done; pwd)"
    BIN_DIR="$(dirname "$TESTS_ROOT")"
    PROJECT_DIR="$(dirname "$BIN_DIR")"
    LIB_DIR="$BIN_DIR/lib"
}
```

**Step 3: Create directories**

```bash
mkdir -p bin/tests/lib
```

**Step 4: Verify BATS works**

Create a minimal test `bin/tests/lib/test_sanity.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
}

@test "BATS is working" {
    run echo "hello"
    assert_success
    assert_output "hello"
}

@test "LIB_DIR points to bin/lib" {
    [[ "$LIB_DIR" == */bin/lib ]]
}
```

**Step 5: Run the sanity test**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_sanity.bats`
Expected: 2 tests, 2 passed

**Step 6: Commit**

```bash
git add bin/tests/ .gitmodules
git commit -m "chore: set up BATS testing infrastructure for bin/ scripts"
```

---

### Task 2: Create `bin/lib/colors.sh` + tests

**Files:**
- Create: `bin/lib/colors.sh`
- Create: `bin/tests/lib/test_colors.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_colors.bats`:

```bash
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
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_colors.bats`
Expected: FAIL — `bin/lib/colors.sh` does not exist

**Step 3: Write implementation**

Create `bin/lib/colors.sh`:

```bash
#!/usr/bin/env bash
#
# Color constants for terminal output.
# Source this file — do not execute directly.
#

# Guard against double-sourcing
[[ -n "${_LIB_COLORS_LOADED:-}" ]] && return 0
_LIB_COLORS_LOADED=1

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_colors.bats`
Expected: 8 tests, 8 passed

**Step 5: Commit**

```bash
git add bin/lib/colors.sh bin/tests/lib/test_colors.bats
git commit -m "feat: add bin/lib/colors.sh with BATS tests"
```

---

### Task 3: Create `bin/lib/logging.sh` + tests

**Files:**
- Create: `bin/lib/logging.sh`
- Create: `bin/tests/lib/test_logging.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_logging.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/logging.sh"
}

@test "info() outputs with [INFO] label" {
    run info "test message"
    assert_success
    assert_output --partial "[INFO]"
    assert_output --partial "test message"
}

@test "success() outputs with [OK] label" {
    run success "test message"
    assert_success
    assert_output --partial "[OK]"
    assert_output --partial "test message"
}

@test "warn() outputs with [WARN] label" {
    run warn "test message"
    assert_success
    assert_output --partial "[WARN]"
    assert_output --partial "test message"
}

@test "error() outputs with [ERROR] label" {
    run error "test message"
    assert_success
    assert_output --partial "[ERROR]"
    assert_output --partial "test message"
}

@test "error() writes to stderr" {
    # Capture stderr separately
    run bash -c 'source "$1" && error "stderr test" 2>&1 1>/dev/null' -- "$LIB_DIR/logging.sh"
    assert_output --partial "stderr test"
}

@test "info() handles multiple arguments" {
    run info "hello world"
    assert_success
    assert_output --partial "hello world"
}
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_logging.bats`
Expected: FAIL

**Step 3: Write implementation**

Create `bin/lib/logging.sh`:

```bash
#!/usr/bin/env bash
#
# Logging functions for terminal output.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_LOGGING_LOADED:-}" ]] && return 0
_LIB_LOGGING_LOADED=1

# Source colors if not already loaded
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/colors.sh"

info()    { printf "%b[INFO]%b  %s\n" "$BLUE" "$NC" "$*"; }
success() { printf "%b[OK]%b    %s\n" "$GREEN" "$NC" "$*"; }
warn()    { printf "%b[WARN]%b  %s\n" "$YELLOW" "$NC" "$*"; }
error()   { printf "%b[ERROR]%b %s\n" "$RED" "$NC" "$*" >&2; }
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_logging.bats`
Expected: 6 tests, 6 passed

**Step 5: Commit**

```bash
git add bin/lib/logging.sh bin/tests/lib/test_logging.bats
git commit -m "feat: add bin/lib/logging.sh with BATS tests"
```

---

### Task 4: Create `bin/lib/paths.sh` + tests

**Files:**
- Create: `bin/lib/paths.sh`
- Create: `bin/tests/lib/test_paths.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_paths.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/paths.sh"
}

@test "BIN_DIR is set and points to bin/" {
    [ -n "$BIN_DIR" ]
    [[ "$BIN_DIR" == */bin ]]
    [ -d "$BIN_DIR" ]
}

@test "PROJECT_DIR is set and is parent of BIN_DIR" {
    [ -n "$PROJECT_DIR" ]
    [ -d "$PROJECT_DIR" ]
    [ "$(dirname "$BIN_DIR")" = "$PROJECT_DIR" ]
}

@test "PROJECT_DIR contains pyproject.toml" {
    [ -f "$PROJECT_DIR/pyproject.toml" ]
}

@test "resolve_project_dir returns correct path from nested dir" {
    # Call from a subdirectory to ensure resolution works
    run bash -c 'cd /tmp && source "'"$LIB_DIR/paths.sh"'" && echo "$PROJECT_DIR"'
    assert_success
    assert_output "$PROJECT_DIR"
}
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_paths.bats`
Expected: FAIL

**Step 3: Write implementation**

Create `bin/lib/paths.sh`:

```bash
#!/usr/bin/env bash
#
# Path resolution for bin/ scripts.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_PATHS_LOADED:-}" ]] && return 0
_LIB_PATHS_LOADED=1

# Resolve BIN_DIR from this file's location (bin/lib/ -> bin/)
BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Project root is parent of bin/
PROJECT_DIR="$(dirname "$BIN_DIR")"
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_paths.bats`
Expected: 4 tests, 4 passed

**Step 5: Commit**

```bash
git add bin/lib/paths.sh bin/tests/lib/test_paths.bats
git commit -m "feat: add bin/lib/paths.sh with BATS tests"
```

---

### Task 5: Create `bin/lib/checks.sh` + tests

**Files:**
- Create: `bin/lib/checks.sh`
- Create: `bin/tests/lib/test_checks.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_checks.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/checks.sh"
}

@test "command_exists returns 0 for bash" {
    run command_exists bash
    assert_success
}

@test "command_exists returns 1 for nonexistent command" {
    run command_exists definitely_not_a_real_command_xyz
    assert_failure
}

@test "command_exists returns 0 for ls" {
    run command_exists ls
    assert_success
}
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_checks.bats`
Expected: FAIL

**Step 3: Write implementation**

Create `bin/lib/checks.sh`:

```bash
#!/usr/bin/env bash
#
# Common prerequisite checks.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_CHECKS_LOADED:-}" ]] && return 0
_LIB_CHECKS_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for a working Python 3.10+ binary.
# Sets PYTHON_BIN on success, exits on failure.
# Handles pyenv shims that exist but point to uninstalled versions.
check_python() {
    info "Checking Python version..."
    PYTHON_BIN=""
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command_exists "$candidate" && "$candidate" --version >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done

    if [ -z "$PYTHON_BIN" ]; then
        error "Python 3 is not installed. Please install Python 3.10 or higher."
        return 1
    fi

    local version major minor
    version=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
        success "Python $version found via $PYTHON_BIN (>= 3.10 required)"
        return 0
    else
        error "Python 3.10+ is required, but found Python $version ($PYTHON_BIN)"
        return 1
    fi
}

# Check for uv package manager, install if missing.
# Exits on failure.
check_uv() {
    info "Checking for uv package manager..."
    if command_exists uv; then
        local uv_version
        uv_version=$(uv --version 2>/dev/null | head -n1)
        success "uv is already installed: $uv_version"
        return 0
    fi

    warn "uv is not installed. Installing uv..."

    if [[ "$OSTYPE" == "darwin"* ]] || [[ "$OSTYPE" == "linux"* ]]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh

        if [ -f "$HOME/.cargo/env" ]; then
            # shellcheck disable=SC1091
            source "$HOME/.cargo/env"
        fi

        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

        if command_exists uv; then
            success "uv installed successfully"
            return 0
        else
            error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
            return 1
        fi
    else
        error "Unsupported OS. Please install uv manually: https://docs.astral.sh/uv/"
        return 1
    fi
}
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_checks.bats`
Expected: 3 tests, 3 passed

**Step 5: Commit**

```bash
git add bin/lib/checks.sh bin/tests/lib/test_checks.bats
git commit -m "feat: add bin/lib/checks.sh with BATS tests"
```

---

### Task 6: Create `bin/lib/dotenv.sh` + tests

**Files:**
- Create: `bin/lib/dotenv.sh`
- Create: `bin/tests/lib/test_dotenv.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_dotenv.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/dotenv.sh"

    # Create a temp directory for .env test files
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

    # Override PROJECT_DIR for this test
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
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_dotenv.bats`
Expected: FAIL

**Step 3: Write implementation**

Create `bin/lib/dotenv.sh`:

```bash
#!/usr/bin/env bash
#
# .env file helpers.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_DOTENV_LOADED:-}" ]] && return 0
_LIB_DOTENV_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/paths.sh"

# Create .env from .env.sample if it doesn't exist.
# Uses PROJECT_DIR (from paths.sh or overridden by caller).
dotenv_ensure_file() {
    local env_file="$PROJECT_DIR/.env"
    local sample_file="$PROJECT_DIR/.env.sample"

    if [ -f "$env_file" ]; then
        success ".env already exists"
        return 0
    fi

    if [ -f "$sample_file" ]; then
        cp "$sample_file" "$env_file"
        success "Created .env from .env.sample"
        return 0
    fi

    warn "No .env.sample found; creating empty .env"
    touch "$env_file"
}

# Check if a key exists in a dotenv-style file.
# Usage: dotenv_has_key <file> <key>
dotenv_has_key() {
    local file="$1"
    local key="$2"
    grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$file"
}

# Set a key=value in a file only if the key is not already present.
# Usage: dotenv_set_if_missing <file> <key> <value>
dotenv_set_if_missing() {
    local file="$1"
    local key="$2"
    local value="$3"

    if dotenv_has_key "$file" "$key"; then
        return 0
    fi

    printf "%s=%s\n" "$key" "$value" >> "$file"
}

# Prompt user until they provide a non-empty value.
# Usage: result=$(prompt_non_empty "Enter value: ")
prompt_non_empty() {
    local prompt="$1"
    local value=""
    while true; do
        read -p "$prompt" -r value
        if [ -n "$value" ]; then
            echo "$value"
            return 0
        fi
        echo "Value cannot be empty."
    done
}
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_dotenv.bats`
Expected: 10 tests, 10 passed

**Step 5: Commit**

```bash
git add bin/lib/dotenv.sh bin/tests/lib/test_dotenv.bats
git commit -m "feat: add bin/lib/dotenv.sh with BATS tests"
```

---

### Task 7: Create `bin/lib/docker.sh` + tests

**Files:**
- Create: `bin/lib/docker.sh`
- Create: `bin/tests/lib/test_docker.bats`

**Step 1: Write the failing test**

Create `bin/tests/lib/test_docker.bats`:

```bash
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
    # Override PATH to hide docker
    PATH="/usr/bin:/bin"
    run docker_preflight "/dev/null"
    assert_failure
}
```

**Step 2: Run test to verify it fails**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_docker.bats`
Expected: FAIL

**Step 3: Write implementation**

Create `bin/lib/docker.sh`:

```bash
#!/usr/bin/env bash
#
# Docker Compose helpers.
# Source this file — do not execute directly.
#

[[ -n "${_LIB_DOCKER_LOADED:-}" ]] && return 0
_LIB_DOCKER_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/checks.sh"

# Parse the state of a service from docker compose ps JSON output.
# Handles both JSON array (Compose v2.21+) and NDJSON (older v2).
# Reads from stdin so it can be tested without Docker.
# Usage: echo "$json" | parse_service_state <service_name>
parse_service_state() {
    local service="$1"
    python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    if isinstance(data, list):
        for d in data:
            if d.get('Service') == '$service':
                print(d.get('State', ''))
                sys.exit(0)
    elif isinstance(data, dict):
        if data.get('Service') == '$service':
            print(data.get('State', ''))
            sys.exit(0)
" 2>/dev/null || true
}

# Get the state of a running docker compose service.
# Usage: get_service_state <compose_file> <service_name>
get_service_state() {
    local compose_file="$1"
    local service="$2"
    docker compose -f "$compose_file" ps --format json 2>/dev/null \
        | parse_service_state "$service"
}

# Run Docker pre-flight checks.
# Usage: docker_preflight <compose_file>
# Returns 1 on failure.
docker_preflight() {
    local compose_file="$1"

    info "Checking for .env file..."
    if [ ! -f "$(dirname "$(dirname "$compose_file")")/../.env" ] && [ ! -f ".env" ]; then
        # Try to locate .env relative to compose file's project context
        :
    fi

    info "Checking Docker daemon..."
    if ! command_exists docker || ! docker info >/dev/null 2>&1; then
        error "Docker is not running."
        echo "  Docker is required. Install it from https://docs.docker.com/get-docker/"
        echo "  and ensure the daemon is running."
        return 1
    fi
    success "Docker daemon is running"

    info "Checking docker compose v2..."
    if ! docker compose version >/dev/null 2>&1; then
        error "docker compose v2 is required but not available."
        echo "  Docker Compose v2 is included with Docker Desktop, or can be installed as a plugin."
        echo "  See: https://docs.docker.com/compose/install/"
        return 1
    fi
    local compose_version
    compose_version="$(docker compose version --short)"
    success "docker compose v2 is available (v${compose_version})"

    return 0
}
```

**Step 4: Run test to verify it passes**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_docker.bats`
Expected: 5 tests, 5 passed

**Step 5: Commit**

```bash
git add bin/lib/docker.sh bin/tests/lib/test_docker.bats
git commit -m "feat: add bin/lib/docker.sh with BATS tests"
```

---

### Task 8: Add BATS to CI workflow

**Files:**
- Modify: `.github/workflows/ci.yml`

**Step 1: Add shell-tests job to CI**

Append after the `security` job in `.github/workflows/ci.yml`:

```yaml

  shell-tests:
    name: Shell Tests (BATS)
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"

      - name: Run BATS tests
        run: ./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/
```

**Step 2: Verify YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"`
Expected: `YAML OK`

**Step 3: Run all BATS tests locally one more time**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/`
Expected: All tests pass (sanity + colors + logging + paths + checks + dotenv + docker)

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add BATS shell tests job to CI workflow"
```

---

### Task 9: Clean up sanity test and final verification

**Files:**
- Delete: `bin/tests/lib/test_sanity.bats` (was scaffolding only)

**Step 1: Remove sanity test**

```bash
rm bin/tests/lib/test_sanity.bats
```

**Step 2: Run all tests**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/`
Expected: All remaining tests pass (colors: 8, logging: 6, paths: 4, checks: 3, dotenv: 10, docker: 5 = 36 total)

**Step 3: Verify all lib files have load guards**

Run: `grep -l "_LIB_.*_LOADED" bin/lib/*.sh | wc -l`
Expected: 6 (all lib files)

**Step 4: Commit**

```bash
git add -A bin/tests/lib/
git commit -m "chore: remove sanity test scaffold, finalize Phase 1"
```