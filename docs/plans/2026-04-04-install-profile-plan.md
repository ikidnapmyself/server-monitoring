---
title: "2026-04-04 Install Profile Plan"
parent: Plans
---

{% raw %}

# Install Profile Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add profile save/load to the installer so configurations can be replicated across machines and re-runs are idempotent.

**Architecture:** A new `bin/lib/profile.sh` provides `profile_save` and `profile_load`. The prompt functions in `bin/lib/prompt.sh` gain auto-accept support (`INSTALL_AUTO_ACCEPT=1`). The dispatcher in `bin/install.sh` parses `--profile`, `--yes`, and `--save-profile` flags before dispatching to modules. Cron and aliases modules export their state variables so `profile_save` can capture them.

**Tech Stack:** Bash, bats-core (tests), existing `bin/lib/` helpers

**Design doc:** `docs/plans/2026-04-04-install-profile-design.md`

---

### Task 1: Add auto-accept support to `bin/lib/prompt.sh`

**Files:**
- Modify: `bin/lib/prompt.sh`
- Test: `bin/tests/lib/test_prompt.bats`

**Step 1: Write failing tests**

Add to `bin/tests/lib/test_prompt.bats`:

```bash
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
```

**Step 2: Run tests to verify they fail**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_prompt.bats`
Expected: FAIL — auto-accept not implemented yet.

**Step 3: Implement auto-accept in prompt functions**

In `prompt_with_default`, add early return after computing `default`:

```bash
    local default="${current:-$fallback}"

    # Auto-accept: return default without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]] && [[ -n "$default" ]]; then
        printf '%s\n' "$default"
        return 0
    fi
```

In `prompt_choice`, add early return after finding `current`:

```bash
    # Auto-accept: return current value without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]] && [[ -n "$current" ]]; then
        printf '%s\n' "$current"
        return 0
    fi
```

In `prompt_yes_no`, add early return after setting `default`:

```bash
    # Auto-accept: return default without prompting
    if [[ "${INSTALL_AUTO_ACCEPT:-0}" == "1" ]]; then
        if [[ "$default" == "default_y" ]]; then
            return 0
        else
            return 1
        fi
    fi
```

**Step 4: Run tests to verify they pass**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_prompt.bats`
Expected: All PASS

**Step 5: Run full bats suite**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/`
Expected: All PASS

**Step 6: Commit**

```bash
git add bin/lib/prompt.sh bin/tests/lib/test_prompt.bats
git commit -m "feat: add INSTALL_AUTO_ACCEPT support to prompt functions"
```

---

### Task 2: Create `bin/lib/profile.sh`

**Files:**
- Create: `bin/lib/profile.sh`
- Test: `bin/tests/lib/test_profile.bats`

**Step 1: Write failing tests**

Create `bin/tests/lib/test_profile.bats`:

```bash
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
    # Set up a fake .env
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

    # Non-sensitive keys should be present
    grep -q "DJANGO_ENV=prod" "$TEST_TMPDIR/.install-profile"
    grep -q "DEPLOY_METHOD=bare" "$TEST_TMPDIR/.install-profile"
    grep -q "CELERY_BROKER_URL=redis://localhost:6379/0" "$TEST_TMPDIR/.install-profile"

    # Sensitive keys must NOT be present
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

    run grep "DJANGO_ENV=prod" "$TEST_TMPDIR/.env"
    assert_success
    run grep "DEPLOY_METHOD=docker" "$TEST_TMPDIR/.env"
    assert_success
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

    run grep "DJANGO_ENV=prod" "$TEST_TMPDIR/.env"
    assert_success
    run grep "CELERY_TASK_ALWAYS_EAGER=0" "$TEST_TMPDIR/.env"
    assert_success
    # Comments should not appear in .env
    ! grep -q "^# name:" "$TEST_TMPDIR/.env"
}

@test "profile_load warns and skips sensitive keys if present" {
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
```

**Step 2: Run tests to verify they fail**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_profile.bats`
Expected: FAIL — profile.sh doesn't exist.

**Step 3: Write `bin/lib/profile.sh`**

```bash
#!/usr/bin/env bash
#
# Install profile helpers — save/load installer configuration.
# Source this file — do not execute directly.
#
# Profiles store non-sensitive .env values and installer state variables
# (cron schedule, alias prefix, etc.) for reproducible installations.
#

[[ -n "${_LIB_PROFILE_LOADED:-}" ]] && return 0
_LIB_PROFILE_LOADED=1

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_LIB_DIR/logging.sh"
source "$_LIB_DIR/dotenv.sh"

# Keys that must never appear in a profile
PROFILE_SENSITIVE_KEYS=(DJANGO_SECRET_KEY WEBHOOK_SECRET_CLUSTER)

# Installer state variables not stored in .env
PROFILE_STATE_KEYS=(CRON_SCHEDULE CRON_AUTO_UPDATE CRON_PUSH_TO_HUB ALIAS_PREFIX)

PROFILE_VERSION=1

# profile_save FILE [NAME]
#
# Read .env + shell state variables, write non-sensitive keys to FILE
# with a metadata header.
profile_save() {
    local file="$1"
    local name="${2:-}"
    local env_file="$PROJECT_DIR/.env"

    # Write metadata header
    {
        echo "# server-maintanence install profile"
        echo "# name: ${name:-$(basename "$file")}"
        echo "# created: $(date -u +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || date +%Y-%m-%dT%H:%M:%S)"
        echo "# hostname: $(hostname 2>/dev/null || echo unknown)"
        echo "# installer_version: $PROFILE_VERSION"
        echo ""
    } > "$file"

    # Write non-sensitive keys from .env
    if [ -f "$env_file" ]; then
        while IFS= read -r line; do
            # Skip comments and blank lines
            [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue

            # Extract key
            local key="${line%%=*}"
            key="${key#"${key%%[![:space:]]*}"}"  # trim leading whitespace

            # Skip sensitive keys
            local sensitive=false
            for sk in "${PROFILE_SENSITIVE_KEYS[@]}"; do
                if [[ "$key" == "$sk" ]]; then
                    sensitive=true
                    break
                fi
            done
            $sensitive && continue

            echo "$line"
        done < "$env_file" >> "$file"
    fi

    # Write installer state variables (if set in environment)
    local has_state=false
    for sk in "${PROFILE_STATE_KEYS[@]}"; do
        if [[ -n "${!sk:-}" ]]; then
            if [[ "$has_state" == false ]]; then
                echo "" >> "$file"
                echo "# Installer state" >> "$file"
                has_state=true
            fi
            printf "%s=%s\n" "$sk" "${!sk}" >> "$file"
        fi
    done

    success "Profile saved to $file"
}

# profile_load FILE
#
# Read profile and write values to .env via dotenv_set.
# Sensitive keys are ignored with a warning.
# Also exports installer state variables into the shell environment.
profile_load() {
    local file="$1"
    local env_file="$PROJECT_DIR/.env"

    if [[ ! -f "$file" ]]; then
        error "Profile not found: $file"
        return 1
    fi

    dotenv_ensure_file

    info "Loading profile: $file"
    local name
    name="$(profile_metadata "$file" "name")"
    [[ -n "$name" ]] && info "Profile name: $name"

    while IFS= read -r line; do
        # Skip comments and blank lines
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue

        local key="${line%%=*}"
        local value="${line#*=}"

        # Skip sensitive keys with warning
        for sk in "${PROFILE_SENSITIVE_KEYS[@]}"; do
            if [[ "$key" == "$sk" ]]; then
                warn "Skipping sensitive key '$key' from profile"
                continue 2
            fi
        done

        # Check if this is an installer state variable
        local is_state=false
        for sk in "${PROFILE_STATE_KEYS[@]}"; do
            if [[ "$key" == "$sk" ]]; then
                is_state=true
                export "$key=$value"
                break
            fi
        done

        # Write to .env if not a state-only variable
        if [[ "$is_state" == false ]]; then
            dotenv_set "$env_file" "$key" "$value"
        fi
    done < "$file"

    success "Profile loaded"
}

# profile_metadata FILE KEY
#
# Read a metadata value from the profile header comments.
# Metadata lines look like: # key: value
profile_metadata() {
    local file="$1"
    local key="$2"

    grep -E "^# ${key}:" "$file" 2>/dev/null \
        | head -1 | sed "s/^# ${key}:[[:space:]]*//"
}
```

**Step 4: Run tests to verify they pass**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_profile.bats`
Expected: All PASS

**Step 5: Run full suite**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/`
Expected: All PASS

**Step 6: Commit**

```bash
git add bin/lib/profile.sh bin/tests/lib/test_profile.bats
git commit -m "feat: add install profile library for save/load configuration"
```

---

### Task 3: Update `bin/install.sh` dispatcher with profile flags

**Files:**
- Modify: `bin/install.sh`
- Test: `bin/tests/test_install.bats`

**Step 1: Write failing tests**

Add to `bin/tests/test_install.bats`:

```bash
@test "install.sh help mentions --profile and --yes" {
    run "$BIN_DIR/install.sh" help
    assert_success
    assert_output --partial "--profile"
    assert_output --partial "--yes"
    assert_output --partial "--save-profile"
}
```

**Step 2: Run test to verify it fails**

**Step 3: Update `bin/install.sh`**

Add flag parsing before the case dispatcher. After the existing `source` lines at the top, add:

```bash
source "$SCRIPT_DIR/lib/prompt.sh"
source "$SCRIPT_DIR/lib/profile.sh"

# ── Flag parsing ─────────────────────────────────────────────────────────────

INSTALL_PROFILE_FILE=""
INSTALL_SAVE_PROFILE=""
INSTALL_PROFILE_NAME=""
export INSTALL_AUTO_ACCEPT="${INSTALL_AUTO_ACCEPT:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            [[ $# -lt 2 ]] && { error "--profile requires a file argument"; exit 1; }
            INSTALL_PROFILE_FILE="$2"
            shift 2
            ;;
        --yes|-y)
            export INSTALL_AUTO_ACCEPT=1
            shift
            ;;
        --save-profile)
            INSTALL_SAVE_PROFILE=1
            if [[ "${2:-}" != "" && "${2:-}" != -* ]]; then
                INSTALL_PROFILE_NAME="$2"
                shift
            fi
            shift
            ;;
        *)
            break
            ;;
    esac
done

# Resolve profile file path
if [[ -n "$INSTALL_PROFILE_FILE" ]]; then
    # Try as name first: .install-profile-<name>
    if [[ ! -f "$INSTALL_PROFILE_FILE" && -f "$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_FILE" ]]; then
        INSTALL_PROFILE_FILE="$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_FILE"
    fi
    profile_load "$INSTALL_PROFILE_FILE"
fi
```

Update `show_usage` to include new flags:

```bash
show_usage() {
    echo ""
    echo "Usage: install.sh [options] [step] [step-options]"
    echo ""
    echo "Options:"
    echo "  --profile FILE    Load saved profile (pre-fills prompts from FILE)"
    echo "  --yes, -y         Accept all defaults without prompting (secrets still prompted)"
    echo "  --save-profile [NAME]  Save configuration to profile after install"
    echo ""
    echo "Steps:"
    ...existing steps...
}
```

After the case dispatcher (at the very end of the file), add profile save logic:

```bash
# ── Post-install: save profile ───────────────────────────────────────────────

if [[ "${INSTALL_SAVE_PROFILE:-}" == "1" ]]; then
    local profile_path="$PROJECT_DIR/.install-profile"
    if [[ -n "$INSTALL_PROFILE_NAME" ]]; then
        profile_path="$PROJECT_DIR/.install-profile-$INSTALL_PROFILE_NAME"
    fi
    profile_save "$profile_path" "$INSTALL_PROFILE_NAME"
elif [[ "${1:-}" == "" && "${INSTALL_AUTO_ACCEPT:-0}" != "1" ]]; then
    # Full install mode: offer to save
    if prompt_yes_no "Save this configuration as a profile?"; then
        local pname
        pname=$(prompt_with_default /dev/null "" "Profile name" "$(hostname 2>/dev/null || echo default)")
        local profile_path="$PROJECT_DIR/.install-profile"
        [[ "$pname" != "$(hostname 2>/dev/null || echo default)" ]] && profile_path="$PROJECT_DIR/.install-profile-$pname"
        profile_save "$profile_path" "$pname"
    fi
fi
```

Note: The save-profile block at the end cannot use `local` since it's not inside a function. Use plain variables or wrap in a function.

**Step 4: Run tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: All PASS

**Step 5: Commit**

```bash
git add bin/install.sh bin/tests/test_install.bats
git commit -m "feat: add --profile, --yes, --save-profile flags to install.sh"
```

---

### Task 4: Export state variables from cron and aliases modules

**Files:**
- Modify: `bin/install/cron.sh`
- Modify: `bin/install/aliases.sh`

**Step 1: Update `bin/install/cron.sh`**

After the cron schedule is chosen (around the line `info "Using schedule: $CRON_SCHEDULE"`), export the state:

```bash
export CRON_SCHEDULE
```

After the auto-update section, export:

```bash
export CRON_AUTO_UPDATE=1  # or 0 based on user choice
```

After the push-to-hub section, export:

```bash
export CRON_PUSH_TO_HUB=1  # or 0 based on user choice
```

The cron module currently sets `CRON_SCHEDULE` as a plain variable. Find where it's set and ensure it's exported. Also, the auto-update and push-to-hub sections use `if [[ $REPLY =~ ^[Yy]$ ]]` patterns — after those blocks, set and export the state variable.

Read `bin/install/cron.sh` fully to find the exact lines. Add `export CRON_AUTO_UPDATE` and `export CRON_PUSH_TO_HUB` after the relevant prompt_yes_no calls.

**Step 2: Update `bin/install/aliases.sh`**

After the prefix is determined (in the `setup` action path), export:

```bash
export ALIAS_PREFIX="$prefix"
```

Read `bin/install/aliases.sh` fully to find where `prefix` is finalized, and add the export there.

**Step 3: Run bats tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/`
Expected: All PASS

**Step 4: Commit**

```bash
git add bin/install/cron.sh bin/install/aliases.sh
git commit -m "feat: export installer state variables for profile save"
```

---

### Task 5: Add `.install-profile*` to `.gitignore`

**Files:**
- Modify: `.gitignore`

**Step 1: Add gitignore entry**

Add to `.gitignore`:

```
# Install profiles (may contain environment-specific config)
.install-profile*
```

**Step 2: Verify**

```bash
touch .install-profile-test
git check-ignore .install-profile-test && echo "IGNORED" || echo "NOT IGNORED"
rm .install-profile-test
```

Expected: IGNORED

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore install profiles"
```

---

### Task 6: Update documentation

**Files:**
- Modify: `bin/README.md` — add profile section to install.sh docs
- Modify: `docs/Installation.md` — add profile usage examples

**Step 1: Update `bin/README.md`**

Add a "Profiles" section under the install.sh documentation:

```markdown
### Profiles

Save and load installer configurations for fleet consistency:

```bash
# Save after install
./bin/install.sh --save-profile prod-web

# Load on another machine (pre-fills all prompts)
./bin/install.sh --profile prod-web

# Fully automated (only prompts for secrets)
./bin/install.sh --profile prod-web --yes
```

Profiles are stored as `.install-profile*` files (gitignored). They contain all non-sensitive .env values plus installer state (cron schedule, alias prefix, etc.). Secrets (`DJANGO_SECRET_KEY`, `WEBHOOK_SECRET_CLUSTER`) are never saved to profiles.
```

**Step 2: Update `docs/Installation.md`**

Add a "Profiles" section with usage examples.

**Step 3: Commit**

```bash
git add bin/README.md docs/Installation.md
git commit -m "docs: add install profile usage documentation"
```

---

### Task 7: Run full test suite and verify

**Step 1: Run bats tests**

```bash
bin/tests/test_helper/bats-core/bin/bats bin/tests/
```

Expected: All PASS

**Step 2: Run pytest**

```bash
uv run pytest
```

Expected: All PASS

**Step 3: Syntax check all modified files**

```bash
for f in bin/lib/profile.sh bin/lib/prompt.sh bin/install.sh bin/install/cron.sh bin/install/aliases.sh; do
    bash -n "$f" && echo "OK: $f" || echo "FAIL: $f"
done
```

Expected: All OK

**Step 4: Smoke test**

```bash
bin/install.sh help
```

Expected: Shows --profile, --yes, --save-profile in usage

{% endraw %}