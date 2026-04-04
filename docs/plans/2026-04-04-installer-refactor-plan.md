---
title: "2026-04-04 Installer Refactor Plan"
parent: Plans
---

{% raw %}

# Installer Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reorganize `bin/install.sh` from a monolith into a subcommand dispatcher with `bin/install/` modules, a shared prompt library, and absorbed standalone scripts.

**Architecture:** `install.sh` becomes a thin dispatcher that sources modules from `bin/install/`. A new `bin/lib/prompt.sh` provides reusable `prompt_with_default` and `prompt_choice` functions that show existing `.env` values as defaults. Old standalone scripts (`deploy-docker.sh`, `deploy-systemd.sh`, `setup_cron.sh`, `setup_aliases.sh`) are deleted after their logic is moved into modules.

**Tech Stack:** Bash, bats-core (tests), existing `bin/lib/` helpers (dotenv.sh, logging.sh, checks.sh, paths.sh, colors.sh, docker.sh)

**Design doc:** `docs/plans/2026-04-04-installer-refactor-design.md`

---

### Task 1: Create `bin/lib/prompt.sh` — shared prompt library

**Files:**
- Create: `bin/lib/prompt.sh`
- Test: `bin/tests/test_prompt.bats`

**Step 1: Write the failing tests**

Create `bin/tests/test_prompt.bats`:

```bash
#!/usr/bin/env bats

setup() {
    load 'test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/prompt.sh"

    TEST_ENV="$(mktemp)"
    echo 'EXISTING_KEY=existing_value' > "$TEST_ENV"
    echo 'EMPTY_KEY=' >> "$TEST_ENV"
}

teardown() {
    rm -f "$TEST_ENV"
}

@test "prompt.sh passes syntax check" {
    run bash -n "$LIB_DIR/prompt.sh"
    assert_success
}

@test "prompt_with_default returns existing .env value on empty input" {
    echo "" | prompt_with_default "$TEST_ENV" "EXISTING_KEY" "Test label"
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && echo '' | prompt_with_default '$TEST_ENV' 'EXISTING_KEY' 'Test label'"
    assert_success
    assert_output --partial "existing_value"
}

@test "prompt_with_default uses fallback when key missing and input empty" {
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && echo '' | prompt_with_default '$TEST_ENV' 'MISSING_KEY' 'Test label' 'fallback_val'"
    assert_success
    assert_output --partial "fallback_val"
}

@test "prompt_with_default uses user input over existing value" {
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && echo 'new_value' | prompt_with_default '$TEST_ENV' 'EXISTING_KEY' 'Test label'"
    assert_success
    assert_output --partial "new_value"
}

@test "prompt_with_default masks value when PROMPT_MASK=1" {
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && PROMPT_MASK=1 echo '' | prompt_with_default '$TEST_ENV' 'EXISTING_KEY' 'Test label'"
    assert_success
    # Should NOT show the actual value in the prompt label
    refute_output --partial "existing_value"
}

@test "prompt_choice returns existing value on empty input" {
    echo 'DEPLOY_METHOD=docker' >> "$TEST_ENV"
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && echo '' | prompt_choice '$TEST_ENV' 'DEPLOY_METHOD' 'Deployment method' 'bare:bare-metal' 'docker:Docker Compose'"
    assert_success
    assert_output --partial "docker"
}

@test "prompt_choice rejects invalid input and uses default" {
    echo 'DEPLOY_METHOD=bare' >> "$TEST_ENV"
    # Send invalid then empty (to accept default)
    run bash -c "source '$LIB_DIR/prompt.sh' && source '$LIB_DIR/dotenv.sh' && printf 'invalid\n\n' | prompt_choice '$TEST_ENV' 'DEPLOY_METHOD' 'Deployment method' 'bare:bare-metal' 'docker:Docker Compose'"
    assert_success
    assert_output --partial "bare"
}
```

**Step 2: Run tests to verify they fail**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_prompt.bats`
Expected: FAIL — `prompt.sh` does not exist.

**Step 3: Write `bin/lib/prompt.sh`**

```bash
#!/usr/bin/env bash
#
# Reusable prompt helpers for installer modules.
# Source this file — do not execute directly.
#
# All prompts show existing .env values as defaults.
# Priority: user input > existing .env value > hardcoded fallback.
#

[[ -n "${_LIB_PROMPT_LOADED:-}" ]] && return 0
_LIB_PROMPT_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"

# prompt_with_default ENV_FILE KEY "label" [fallback]
#
# Reads KEY from ENV_FILE. Shows current value (or fallback) in brackets.
# User presses Enter to keep, or types a new value.
# Prints the final value to stdout.
#
# If PROMPT_MASK=1, shows "••••••••" instead of actual value.
# If no existing value, no fallback, and empty input: loops until non-empty.
prompt_with_default() {
    local env_file="$1"
    local key="$2"
    local label="$3"
    local fallback="${4:-}"

    # Read existing value from .env
    local existing=""
    if [ -f "$env_file" ]; then
        existing=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$env_file" 2>/dev/null \
            | tail -1 | sed "s/^[[:space:]]*${key}[[:space:]]*=//")
    fi

    local default_val="${existing:-$fallback}"

    local display_val="$default_val"
    if [ "${PROMPT_MASK:-0}" = "1" ] && [ -n "$default_val" ]; then
        display_val="$(printf '%.0s•' {1..8})"
    fi

    local value=""
    while true; do
        if [ -n "$default_val" ]; then
            read -r -p "$label [$display_val]: " value
            value="${value:-$default_val}"
        else
            read -r -p "$label: " value
        fi

        if [ -n "$value" ]; then
            printf '%s\n' "$value"
            return 0
        fi

        # No default and no input — require something
        echo "Value cannot be empty."
    done
}

# prompt_choice ENV_FILE KEY "label" "opt1:desc1" "opt2:desc2" ...
#
# Shows a numbered menu. Reads current KEY from ENV_FILE as default.
# Validates input against known options.
# Prints the selected option value to stdout.
prompt_choice() {
    local env_file="$1"
    local key="$2"
    local label="$3"
    shift 3

    local options=("$@")
    local opt_values=()
    local opt_descs=()
    local i=1

    for opt in "${options[@]}"; do
        local val="${opt%%:*}"
        local desc="${opt#*:}"
        opt_values+=("$val")
        opt_descs+=("$desc")
        i=$((i + 1))
    done

    # Read existing value
    local existing=""
    if [ -f "$env_file" ]; then
        existing=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$env_file" 2>/dev/null \
            | tail -1 | sed "s/^[[:space:]]*${key}[[:space:]]*=//")
    fi

    # Find default index (1-based)
    local default_idx=""
    for idx in "${!opt_values[@]}"; do
        if [ "${opt_values[$idx]}" = "$existing" ]; then
            default_idx=$((idx + 1))
            break
        fi
    done

    echo ""
    echo "$label:"
    for idx in "${!opt_values[@]}"; do
        local num=$((idx + 1))
        local marker=""
        if [ "$num" = "${default_idx:-}" ]; then
            marker=" (current)"
        fi
        echo "  $num) ${opt_values[$idx]} — ${opt_descs[$idx]}${marker}"
    done
    echo ""

    local choice_prompt="Enter choice [1-${#opt_values[@]}]"
    if [ -n "$default_idx" ]; then
        choice_prompt="$choice_prompt (default: $default_idx)"
    fi

    while true; do
        read -r -p "$choice_prompt: " input

        # Empty input — use default if available
        if [ -z "$input" ] && [ -n "$default_idx" ]; then
            printf '%s\n' "${opt_values[$((default_idx - 1))]}"
            return 0
        fi

        # Numeric input
        if [[ "$input" =~ ^[0-9]+$ ]] && [ "$input" -ge 1 ] && [ "$input" -le "${#opt_values[@]}" ]; then
            printf '%s\n' "${opt_values[$((input - 1))]}"
            return 0
        fi

        # Text input — match against option values
        for idx in "${!opt_values[@]}"; do
            if [ "$input" = "${opt_values[$idx]}" ]; then
                printf '%s\n' "${opt_values[$idx]}"
                return 0
            fi
        done

        warn "Invalid choice '$input'. Try again."
    done
}

# prompt_yes_no "question" [default_y|default_n]
#
# Asks a yes/no question. Returns 0 for yes, 1 for no.
# If ENV_FILE and KEY are set in the caller, reads existing state.
prompt_yes_no() {
    local question="$1"
    local default="${2:-default_n}"

    local hint="y/N"
    [ "$default" = "default_y" ] && hint="Y/n"

    read -r -p "$question [$hint]: " -n 1 reply
    echo ""

    if [ "$default" = "default_y" ]; then
        [[ -z "${reply:-}" || "${reply:-}" =~ ^[Yy]$ ]]
    else
        [[ "${reply:-}" =~ ^[Yy]$ ]]
    fi
}
```

**Step 4: Run tests to verify they pass**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_prompt.bats`
Expected: All PASS

**Step 5: Commit**

```bash
git add bin/lib/prompt.sh bin/tests/test_prompt.bats
git commit -m "feat: add shared prompt library for installer modules"
```

---

### Task 2: Create `bin/install/env.sh` — environment + core .env setup

**Files:**
- Create: `bin/install/env.sh`
- Test: `bin/tests/test_install.bats` (add tests)

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/env.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/env.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: FAIL — file not found.

**Step 3: Write `bin/install/env.sh`**

Extract the env/deploy-method selection and `dotenv_prompt_setup` from current `bin/install.sh:25-153`. Rewrite prompts to use `prompt_choice` and `prompt_with_default`.

```bash
#!/usr/bin/env bash
#
# Installer module: environment and core .env configuration.
# Sourced by install.sh — do not execute directly.
#
# Configures: DJANGO_ENV, DEPLOY_METHOD, DJANGO_DEBUG,
#             DJANGO_ALLOWED_HOSTS, DJANGO_SECRET_KEY
#

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/prompt.sh"
source "$_LIB_DIR/checks.sh"

local_env_file="$PROJECT_DIR/.env"
dotenv_ensure_file

echo ""
echo "============================================"
echo "   Environment Setup"
echo "============================================"
echo ""

# --- DJANGO_ENV ---
DJANGO_ENV=$(prompt_choice "$local_env_file" "DJANGO_ENV" "Select environment" \
    "dev:development (DEBUG=1, eager tasks)" \
    "prod:production (DEBUG=0, real secret key)")
dotenv_set "$local_env_file" "DJANGO_ENV" "$DJANGO_ENV"
info "Environment: $DJANGO_ENV"

# --- DEPLOY_METHOD ---
DEPLOY_METHOD=$(prompt_choice "$local_env_file" "DEPLOY_METHOD" "Select deployment method" \
    "bare:bare-metal (Python + uv, systemd or runserver)" \
    "docker:Docker Compose stack")
dotenv_set "$local_env_file" "DEPLOY_METHOD" "$DEPLOY_METHOD"
info "Deployment method: $DEPLOY_METHOD"

echo ""
echo "============================================"
echo "   Core .env Configuration"
echo "============================================"
echo ""

info "Existing values shown in brackets — press Enter to keep."

# --- DJANGO_DEBUG ---
if [ "$DJANGO_ENV" = "prod" ] && [ "$DEPLOY_METHOD" = "bare" ]; then
    dotenv_set "$local_env_file" "DJANGO_DEBUG" "0"
    info "DJANGO_DEBUG=0 (forced for production bare-metal)"
else
    local default_debug="1"
    [ "$DJANGO_ENV" = "prod" ] && default_debug="0"
    DEBUG_VAL=$(prompt_with_default "$local_env_file" "DJANGO_DEBUG" "DJANGO_DEBUG (1=on, 0=off)" "$default_debug")
    dotenv_set "$local_env_file" "DJANGO_DEBUG" "$DEBUG_VAL"
fi

# --- DJANGO_ALLOWED_HOSTS ---
local hosts_fallback="localhost,127.0.0.1"
[ "$DJANGO_ENV" = "prod" ] && hosts_fallback=""
HOSTS_VAL=$(prompt_with_default "$local_env_file" "DJANGO_ALLOWED_HOSTS" "DJANGO_ALLOWED_HOSTS (comma-separated)" "$hosts_fallback")
dotenv_set "$local_env_file" "DJANGO_ALLOWED_HOSTS" "$HOSTS_VAL"

# --- DJANGO_SECRET_KEY ---
existing_secret=$(grep -E "^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DJANGO_SECRET_KEY[[:space:]]*=//")

if [ -n "$existing_secret" ]; then
    info "DJANGO_SECRET_KEY is already set."
    if prompt_yes_no "Regenerate DJANGO_SECRET_KEY?"; then
        existing_secret=""
    fi
fi

if [ -z "$existing_secret" ]; then
    if prompt_yes_no "Generate a secure DJANGO_SECRET_KEY automatically?" "default_y"; then
        if command_exists python3; then
            key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
            dotenv_set "$local_env_file" "DJANGO_SECRET_KEY" "$key"
            success "DJANGO_SECRET_KEY generated and saved"
        else
            error "python3 not available; cannot auto-generate."
            PROMPT_MASK=1 SECRET_VAL=$(prompt_with_default "$local_env_file" "DJANGO_SECRET_KEY" "Paste DJANGO_SECRET_KEY")
            dotenv_set "$local_env_file" "DJANGO_SECRET_KEY" "$SECRET_VAL"
        fi
    elif [ "$DJANGO_ENV" = "prod" ]; then
        PROMPT_MASK=1 SECRET_VAL=$(prompt_with_default "$local_env_file" "DJANGO_SECRET_KEY" "Paste DJANGO_SECRET_KEY")
        dotenv_set "$local_env_file" "DJANGO_SECRET_KEY" "$SECRET_VAL"
    else
        warn "DJANGO_SECRET_KEY not set. Set it manually before production use."
    fi
fi

success "Environment setup complete"
```

**Step 4: Run tests to verify they pass**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: All PASS (syntax check)

**Step 5: Commit**

```bash
git add bin/install/env.sh bin/tests/test_install.bats
git commit -m "feat: add install/env.sh — environment and core .env module"
```

---

### Task 3: Create `bin/install/celery.sh` — Celery/Redis configuration

**Files:**
- Create: `bin/install/celery.sh`
- Test: `bin/tests/test_install.bats` (add syntax test)

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/celery.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/celery.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: FAIL

**Step 3: Write `bin/install/celery.sh`**

Extract Celery config from `bin/install.sh:112-151`. Rewrite with `prompt_with_default`.

```bash
#!/usr/bin/env bash
#
# Installer module: Celery / Redis broker configuration.
# Sourced by install.sh — do not execute directly.
#

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/prompt.sh"

local_env_file="$PROJECT_DIR/.env"
dotenv_ensure_file

echo ""
echo "============================================"
echo "   Celery / Redis Setup"
echo "============================================"
echo ""

# Read current axes from .env
django_env=$(grep -E "^[[:space:]]*DJANGO_ENV[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DJANGO_ENV[[:space:]]*=//")
deploy_method=$(grep -E "^[[:space:]]*DEPLOY_METHOD[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DEPLOY_METHOD[[:space:]]*=//")

django_env="${django_env:-dev}"
deploy_method="${deploy_method:-bare}"

if [ "$deploy_method" = "docker" ]; then
    dotenv_set_if_missing "$local_env_file" "CELERY_TASK_ALWAYS_EAGER" "0"
    info "CELERY_BROKER_URL is managed by Docker Compose — skipping."
    success "Celery setup complete (Docker mode)"
    return 0 2>/dev/null || exit 0
fi

# bare-metal
if [ "$django_env" = "prod" ]; then
    BROKER_VAL=$(prompt_with_default "$local_env_file" "CELERY_BROKER_URL" "CELERY_BROKER_URL (e.g. redis://redis:6379/0)")
    dotenv_set "$local_env_file" "CELERY_BROKER_URL" "$BROKER_VAL"

    if prompt_yes_no "Set CELERY_RESULT_BACKEND?"; then
        BACKEND_VAL=$(prompt_with_default "$local_env_file" "CELERY_RESULT_BACKEND" "CELERY_RESULT_BACKEND (e.g. redis://redis:6379/1)")
        dotenv_set "$local_env_file" "CELERY_RESULT_BACKEND" "$BACKEND_VAL"
    fi

    dotenv_set_if_missing "$local_env_file" "CELERY_TASK_ALWAYS_EAGER" "0"
else
    # dev + bare: offer eager toggle
    EAGER_VAL=$(prompt_choice "$local_env_file" "CELERY_TASK_ALWAYS_EAGER" "Run Celery tasks eagerly (no broker needed)?" \
        "1:Yes — eager mode (no Redis required)" \
        "0:No — use a real broker")
    dotenv_set "$local_env_file" "CELERY_TASK_ALWAYS_EAGER" "$EAGER_VAL"

    if [ "$EAGER_VAL" = "0" ]; then
        BROKER_VAL=$(prompt_with_default "$local_env_file" "CELERY_BROKER_URL" "CELERY_BROKER_URL" "redis://localhost:6379/0")
        dotenv_set "$local_env_file" "CELERY_BROKER_URL" "$BROKER_VAL"
    fi
fi

success "Celery setup complete"
```

**Step 4: Run tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: PASS

**Step 5: Commit**

```bash
git add bin/install/celery.sh bin/tests/test_install.bats
git commit -m "feat: add install/celery.sh — Celery/Redis config module"
```

---

### Task 4: Create `bin/install/cluster.sh` — cluster role configuration

**Files:**
- Create: `bin/install/cluster.sh`
- Test: `bin/tests/test_install.bats` (add syntax test)

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/cluster.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cluster.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/cluster.sh`**

Extract from `bin/install.sh:160-227` (`dotenv_prompt_cluster`). Rewrite with `prompt_choice`, `prompt_with_default`, `prompt_yes_no`.

```bash
#!/usr/bin/env bash
#
# Installer module: cluster role configuration.
# Sourced by install.sh — do not execute directly.
#

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/prompt.sh"

local_env_file="$PROJECT_DIR/.env"
dotenv_ensure_file

echo ""
echo "============================================"
echo "   Cluster Setup"
echo "============================================"
echo ""

# Check if already configured
existing_role=""
if dotenv_has_value "$local_env_file" "HUB_URL" || dotenv_has_value "$local_env_file" "CLUSTER_ENABLED"; then
    existing_role="configured"
fi

local default_yn="default_n"
[ -n "$existing_role" ] && default_yn="default_y"

if ! prompt_yes_no "Configure this instance for multi-instance (cluster) mode?" "$default_yn"; then
    info "Skipping cluster setup."
    return 0 2>/dev/null || exit 0
fi

# --- Cluster role ---
CLUSTER_ROLE=$(prompt_choice "$local_env_file" "CLUSTER_ROLE" "Select cluster role" \
    "agent:run checkers locally, push results to a hub" \
    "hub:accept alerts from remote agents" \
    "both:agent + hub")
dotenv_set "$local_env_file" "CLUSTER_ROLE" "$CLUSTER_ROLE"

# --- Agent or both: HUB_URL and INSTANCE_ID ---
if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    HUB_VAL=$(prompt_with_default "$local_env_file" "HUB_URL" "HUB_URL (e.g. https://monitoring-hub.example.com)")
    dotenv_set "$local_env_file" "HUB_URL" "$HUB_VAL"

    local default_id
    default_id="$(hostname 2>/dev/null || echo "")"
    INSTANCE_VAL=$(prompt_with_default "$local_env_file" "INSTANCE_ID" "INSTANCE_ID" "$default_id")
    dotenv_set "$local_env_file" "INSTANCE_ID" "$INSTANCE_VAL"
fi

# --- Hub or both: CLUSTER_ENABLED ---
if [ "$CLUSTER_ROLE" = "hub" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    dotenv_set "$local_env_file" "CLUSTER_ENABLED" "1"
    success "CLUSTER_ENABLED=1"
fi

# --- All roles: shared secret ---
PROMPT_MASK=1 SECRET_VAL=$(prompt_with_default "$local_env_file" "WEBHOOK_SECRET_CLUSTER" "WEBHOOK_SECRET_CLUSTER (shared secret)")
dotenv_set "$local_env_file" "WEBHOOK_SECRET_CLUSTER" "$SECRET_VAL"

success "Cluster configuration saved (role: $CLUSTER_ROLE)"

# --- Agent: verify with dry-run ---
if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
    echo ""
    info "Running push_to_hub --dry-run to verify configuration..."
    if uv run python manage.py push_to_hub --dry-run 2>&1; then
        success "Dry run succeeded — agent is configured correctly"
    else
        warn "Dry run failed — check HUB_URL and try: uv run python manage.py push_to_hub --dry-run"
    fi
fi
```

**Step 4: Run tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: PASS

**Step 5: Commit**

```bash
git add bin/install/cluster.sh bin/tests/test_install.bats
git commit -m "feat: add install/cluster.sh — cluster role config module"
```

---

### Task 5: Create `bin/install/deps.sh` — dependency installation

**Files:**
- Create: `bin/install/deps.sh`
- Test: `bin/tests/test_install.bats` (add syntax test)

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/deps.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deps.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/deps.sh`**

Extract from `bin/install.sh:303-318`.

```bash
#!/usr/bin/env bash
#
# Installer module: dependency installation via uv.
# Sourced by install.sh — do not execute directly.
#

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"
source "$_LIB_DIR/checks.sh"

local_env_file="$PROJECT_DIR/.env"

echo ""
echo "============================================"
echo "   Dependency Installation"
echo "============================================"
echo ""

deploy_method=$(grep -E "^[[:space:]]*DEPLOY_METHOD[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DEPLOY_METHOD[[:space:]]*=//")

if [ "${deploy_method:-bare}" = "docker" ]; then
    info "Docker mode — dependencies are managed inside the container."
    info "Skipping uv sync."
    return 0 2>/dev/null || exit 0
fi

check_python || { error "Python check failed. Fix before continuing."; return 1 2>/dev/null || exit 1; }
check_uv || { error "uv check failed. Fix before continuing."; return 1 2>/dev/null || exit 1; }

django_env=$(grep -E "^[[:space:]]*DJANGO_ENV[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DJANGO_ENV[[:space:]]*=//")

if [ "${django_env:-dev}" = "dev" ]; then
    info "Installing dependencies (including dev extras)..."
    uv sync --all-extras --dev
else
    info "Installing dependencies (production only)..."
    uv sync
fi

success "Dependencies installed"
```

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add bin/install/deps.sh bin/tests/test_install.bats
git commit -m "feat: add install/deps.sh — uv dependency install module"
```

---

### Task 6: Create `bin/install/migrate.sh` — Django migrations + checks

**Files:**
- Create: `bin/install/migrate.sh`
- Test: `bin/tests/test_install.bats` (add syntax test)

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/migrate.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/migrate.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/migrate.sh`**

Extract from `bin/install.sh:320-331`.

```bash
#!/usr/bin/env bash
#
# Installer module: Django migrations and system checks.
# Sourced by install.sh — do not execute directly.
#

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="$(cd "$_INSTALL_DIR/../lib" && pwd)"

source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"

local_env_file="$PROJECT_DIR/.env"

deploy_method=$(grep -E "^[[:space:]]*DEPLOY_METHOD[[:space:]]*=" "$local_env_file" 2>/dev/null \
    | tail -1 | sed "s/^[[:space:]]*DEPLOY_METHOD[[:space:]]*=//")

if [ "${deploy_method:-bare}" = "docker" ]; then
    info "Docker mode — migrations run inside the container."
    info "Skipping migrate."
    return 0 2>/dev/null || exit 0
fi

echo ""
echo "============================================"
echo "   Database Migrations & System Checks"
echo "============================================"
echo ""

info "Running database migrations..."
uv run python manage.py migrate
success "Migrations applied"

info "Running Django system checks..."
if uv run python manage.py check; then
    success "All system checks passed"
else
    warn "System checks reported issues (see above). You may want to address them."
fi
```

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add bin/install/migrate.sh bin/tests/test_install.bats
git commit -m "feat: add install/migrate.sh — migrations and system checks module"
```

---

### Task 7: Create `bin/install/cron.sh` — absorb `setup_cron.sh`

**Files:**
- Create: `bin/install/cron.sh`
- Modify: `bin/tests/test_install.bats` (add syntax test)
- Delete later: `bin/setup_cron.sh`, `bin/tests/test_setup_cron.bats`

**Step 1: Write the failing test**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/cron.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/cron.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/cron.sh`**

Move full content from `bin/setup_cron.sh` into this module. Rewrite schedule selection to use `prompt_choice`. Keep auto-update and cluster push logic. Replace `SCRIPT_DIR` references with correct paths.

The module should:
- Use `prompt_choice` for cron schedule (existing crontab value as default if found)
- Use `prompt_yes_no` for auto-update and push-to-hub
- Write through standard crontab manipulation (same logic as current)

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add bin/install/cron.sh bin/tests/test_install.bats
git commit -m "feat: add install/cron.sh — absorb setup_cron.sh"
```

---

### Task 8: Create `bin/install/aliases.sh` — absorb `setup_aliases.sh`

**Files:**
- Create: `bin/install/aliases.sh`
- Modify: `bin/tests/test_install.bats` (add tests)
- Delete later: `bin/setup_aliases.sh`, `bin/tests/test_setup_aliases.bats`

**Step 1: Write the failing tests**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/aliases.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/aliases.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/aliases.sh`**

Move full content from `bin/setup_aliases.sh` into this module. This is the one module that needs to support extra flags (`--remove`, `--list`, `--help`, `--prefix`) because it was the most featureful standalone script.

The module should parse `$INSTALL_ARGS` (set by the dispatcher from `$2 $3 ...`) for these flags, or use `prompt_with_default` for the prefix in interactive mode.

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add bin/install/aliases.sh bin/tests/test_install.bats
git commit -m "feat: add install/aliases.sh — absorb setup_aliases.sh"
```

---

### Task 9: Create `bin/install/deploy.sh` — absorb deploy-docker.sh and deploy-systemd.sh

**Files:**
- Create: `bin/install/deploy.sh`
- Modify: `bin/tests/test_install.bats` (add tests)
- Delete later: `bin/deploy-docker.sh`, `bin/deploy-systemd.sh`, `bin/tests/test_deploy_docker.bats`, `bin/tests/test_deploy_systemd.bats`

**Step 1: Write the failing tests**

Add to `bin/tests/test_install.bats`:

```bash
@test "install/deploy.sh passes syntax check" {
    run bash -n "$BIN_DIR/install/deploy.sh"
    assert_success
}
```

**Step 2: Run test to verify it fails**

**Step 3: Write `bin/install/deploy.sh`**

Reads `DEPLOY_METHOD` from `.env`, then runs the appropriate deploy flow:
- **docker**: pre-flight checks (`docker_preflight`), build, `docker compose up -d`, health verification loop, summary. Absorb full `bin/deploy-docker.sh` logic.
- **bare + prod**: pre-flight checks (root, .venv, env file, redis), copy unit files, `systemctl daemon-reload`, migrations, collectstatic, enable+start services, health verification loop, summary. Absorb full `bin/deploy-systemd.sh` logic.
- **bare + dev**: print "dev mode — run `uv run python manage.py runserver`" and return.

No `exec` — all deploy logic runs in-process and returns.

**Step 4: Run tests**

**Step 5: Commit**

```bash
git add bin/install/deploy.sh bin/tests/test_install.bats
git commit -m "feat: add install/deploy.sh — absorb deploy-docker.sh and deploy-systemd.sh"
```

---

### Task 10: Rewrite `bin/install.sh` as dispatcher

**Files:**
- Modify: `bin/install.sh`
- Test: `bin/tests/test_install.bats`

**Step 1: Write the failing tests**

Add to `bin/tests/test_install.bats`:

```bash
@test "install.sh help shows available subcommands" {
    run "$BIN_DIR/install.sh" help
    assert_success
    assert_output --partial "env"
    assert_output --partial "celery"
    assert_output --partial "cluster"
    assert_output --partial "deps"
    assert_output --partial "migrate"
    assert_output --partial "cron"
    assert_output --partial "aliases"
    assert_output --partial "deploy"
}

@test "install.sh rejects unknown subcommand" {
    run "$BIN_DIR/install.sh" foobar
    assert_failure
    assert_output --partial "Unknown step"
}
```

**Step 2: Run test to verify they fail**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`

**Step 3: Rewrite `bin/install.sh`**

Replace entire file with the thin dispatcher:

```bash
#!/bin/bash
#
# Installer for server-maintanence
#
# Usage:
#   install.sh              Run full installation (all steps)
#   install.sh <step>       Run a single step
#   install.sh help         Show available steps
#
# Steps:
#   env       Environment and core .env configuration
#   celery    Celery / Redis broker setup
#   cluster   Multi-instance cluster role configuration
#   deps      Install Python dependencies via uv
#   migrate   Run Django migrations and system checks
#   cron      Set up cron jobs for health checks
#   aliases   Set up shell aliases
#   deploy    Deploy via Docker Compose or systemd
#

set -e

# Source shared libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/logging.sh"
source "$SCRIPT_DIR/lib/paths.sh"
source "$SCRIPT_DIR/lib/dotenv.sh"

INSTALL_MOD_DIR="$SCRIPT_DIR/install"

cd "$PROJECT_DIR"

# ── Help ─────────────────────────────────────────────────────────────────────

show_usage() {
    echo ""
    echo "Usage: install.sh [step]"
    echo ""
    echo "Steps:"
    echo "  env       Environment and core .env configuration"
    echo "  celery    Celery / Redis broker setup"
    echo "  cluster   Multi-instance cluster role configuration"
    echo "  deps      Install Python dependencies via uv"
    echo "  migrate   Run Django migrations and system checks"
    echo "  cron      Set up cron jobs for health checks"
    echo "  aliases   Set up shell aliases"
    echo "  deploy    Deploy via Docker Compose or systemd"
    echo ""
    echo "  help      Show this message"
    echo ""
    echo "Run with no arguments for the full guided installation."
    echo ""
}

# ── Full flow ────────────────────────────────────────────────────────────────

run_all() {
    echo ""
    echo "============================================"
    echo "   server-maintanence Installer"
    echo "============================================"
    echo ""

    source "$INSTALL_MOD_DIR/env.sh"
    source "$INSTALL_MOD_DIR/celery.sh"
    source "$INSTALL_MOD_DIR/cluster.sh"
    source "$INSTALL_MOD_DIR/deps.sh"
    source "$INSTALL_MOD_DIR/migrate.sh"
    source "$INSTALL_MOD_DIR/cron.sh"
    source "$INSTALL_MOD_DIR/aliases.sh"
    source "$INSTALL_MOD_DIR/deploy.sh"

    echo ""
    echo "============================================"
    echo -e "${GREEN}   Installation Complete!${NC}"
    echo "============================================"
    echo ""
    info "Your server-maintanence project is now set up."
    echo ""
    echo "Quick commands:"
    echo "  - Run health checks:    uv run python manage.py check_health"
    echo "  - List checkers:        uv run python manage.py check_health --list"
    echo "  - Start server:         uv run python manage.py runserver"
    echo "  - Run tests:            uv run pytest"
    echo ""
}

# ── Dispatcher ───────────────────────────────────────────────────────────────

case "${1:-}" in
    env)      source "$INSTALL_MOD_DIR/env.sh"     ;;
    celery)   source "$INSTALL_MOD_DIR/celery.sh"  ;;
    cluster)  source "$INSTALL_MOD_DIR/cluster.sh" ;;
    deps)     source "$INSTALL_MOD_DIR/deps.sh"    ;;
    migrate)  source "$INSTALL_MOD_DIR/migrate.sh" ;;
    cron)     source "$INSTALL_MOD_DIR/cron.sh"    ;;
    aliases)  source "$INSTALL_MOD_DIR/aliases.sh" ;;
    deploy)   source "$INSTALL_MOD_DIR/deploy.sh"  ;;
    help|-h|--help) show_usage                     ;;
    "")       run_all                              ;;
    *)        error "Unknown step: $1"; show_usage; exit 1 ;;
esac
```

**Step 4: Run all tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: All PASS

**Step 5: Commit**

```bash
git add bin/install.sh bin/tests/test_install.bats
git commit -m "feat: rewrite install.sh as subcommand dispatcher"
```

---

### Task 11: Delete old standalone scripts and their tests

**Files:**
- Delete: `bin/deploy-docker.sh`
- Delete: `bin/deploy-systemd.sh`
- Delete: `bin/setup_cron.sh`
- Delete: `bin/setup_aliases.sh`
- Delete: `bin/tests/test_deploy_docker.bats`
- Delete: `bin/tests/test_deploy_systemd.bats`
- Delete: `bin/tests/test_setup_cron.bats`
- Delete: `bin/tests/test_setup_aliases.bats`

**Step 1: Delete files**

```bash
git rm bin/deploy-docker.sh bin/deploy-systemd.sh bin/setup_cron.sh bin/setup_aliases.sh
git rm bin/tests/test_deploy_docker.bats bin/tests/test_deploy_systemd.bats
git rm bin/tests/test_setup_cron.bats bin/tests/test_setup_aliases.bats
```

**Step 2: Run all bats tests to confirm nothing breaks**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/`
Expected: All PASS

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: remove standalone scripts absorbed into install.sh"
```

---

### Task 12: Update all references in shell scripts

**Files:**
- Modify: `bin/cli.sh:40` — change `bin/setup_aliases.sh` to `bin/install.sh aliases`
- Modify: `bin/cli/install_menu.sh:32` — change `$SCRIPT_DIR/setup_aliases.sh` to `$SCRIPT_DIR/install.sh aliases`
- Modify: `bin/lib/health_check.sh:222` — change `bin/setup_aliases.sh` to `bin/install.sh aliases`
- Modify: `bin/set_production.sh:111` — change `sudo bin/deploy-systemd.sh` to `bin/install.sh deploy`

**Step 1: Make edits**

`bin/cli.sh:40`:
```
- echo -e "${YELLOW}Tip:${NC} Run ${CYAN}bin/setup_aliases.sh${NC} ..."
+ echo -e "${YELLOW}Tip:${NC} Run ${CYAN}bin/install.sh aliases${NC} ..."
```

`bin/cli/install_menu.sh:32`:
```
- run_command "$SCRIPT_DIR/setup_aliases.sh" "Setting up shell aliases"
+ run_command "$SCRIPT_DIR/install.sh aliases" "Setting up shell aliases"
```

`bin/lib/health_check.sh:222`:
```
- hc_warn "aliases" "Shell aliases not configured (run: bin/setup_aliases.sh)"
+ hc_warn "aliases" "Shell aliases not configured (run: bin/install.sh aliases)"
```

`bin/set_production.sh:111`:
```
- echo "  - Deploy systemd:    sudo bin/deploy-systemd.sh"
+ echo "  - Deploy systemd:    bin/install.sh deploy"
```

**Step 2: Run bats tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/`
Expected: All PASS

**Step 3: Commit**

```bash
git add bin/cli.sh bin/cli/install_menu.sh bin/lib/health_check.sh bin/set_production.sh
git commit -m "refactor: update shell script references to new install.sh subcommands"
```

---

### Task 13: Update documentation

**Files:**
- Modify: `README.md:150`
- Modify: `bin/README.md:9,40-56,132-162,218`
- Modify: `docs/Installation.md:59-60,71,86,104,127,134,249`
- Modify: `docs/Deployment.md:188,363`
- Modify: `docs/Setup-Guide.md:216`
- Modify: `CLAUDE.md` (if needed)

**Step 1: Update all doc references**

Replace all occurrences:
- `./bin/setup_aliases.sh` → `./bin/install.sh aliases`
- `./bin/setup_cron.sh` → `./bin/install.sh cron`
- `./bin/deploy-docker.sh` → `./bin/install.sh deploy`
- `sudo ./bin/deploy-systemd.sh` → `./bin/install.sh deploy`
- `bin/deploy-systemd.sh` → `bin/install.sh deploy`
- `bin/setup_cron.sh` → `bin/install.sh cron`
- `bin/setup_aliases.sh` → `bin/install.sh aliases`

Also update `bin/README.md` to:
- Remove standalone sections for `setup_aliases.sh`, `deploy-docker.sh`, `deploy-systemd.sh`, `setup_cron.sh`
- Add a section documenting `install.sh` subcommands

Update `docs/Installation.md` aliases section to show:
```
./bin/install.sh aliases --prefix maint
./bin/install.sh aliases --remove
```

**Step 2: Verify no stale references remain**

Run: `grep -r 'setup_cron\.sh\|setup_aliases\.sh\|deploy-docker\.sh\|deploy-systemd\.sh' --include='*.md' --include='*.sh' .`
Expected: No matches (excluding `.worktrees/` and `docs/plans/` historical records)

**Step 3: Commit**

```bash
git add README.md bin/README.md docs/Installation.md docs/Deployment.md docs/Setup-Guide.md CLAUDE.md
git commit -m "docs: update all references to new install.sh subcommands"
```

---

### Task 14: Run full test suite and verify

**Step 1: Run bats tests**

```bash
bin/tests/test_helper/bats-core/bin/bats bin/tests/
```

Expected: All PASS

**Step 2: Run Python test suite**

```bash
uv run pytest
```

Expected: All PASS

**Step 3: Run syntax check on all new files**

```bash
for f in bin/install/*.sh bin/lib/prompt.sh; do bash -n "$f" && echo "OK: $f"; done
```

Expected: All OK

**Step 4: Smoke test install.sh help**

```bash
bin/install.sh help
```

Expected: Shows all subcommands

**Step 5: Smoke test a single subcommand (read-only)**

```bash
bin/install.sh help
```

Expected: No errors, clean output

{% endraw %}