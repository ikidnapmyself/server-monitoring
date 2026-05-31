---
title: "Update Aliases Follow-up Implementation Plan"
parent: Plans
---

# Update Aliases Follow-up Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the duplicate prefix-detection logic in `_up_sync_aliases`, add a `--no-profile` flag to `_aliases_main` so the update path doesn't rewrite the user's shell profile on every run, and cover the previously-untested code paths.

**Architecture:** Two-file edit. (1) `bin/install/aliases.sh`: enhance `_aliases_read_existing_prefix` with an alias-name fallback (moved from the soon-to-be-deleted helper in `update.sh`), add a `--no-profile` flag to `_aliases_main`, update `_aliases_show_help`. (2) `bin/lib/update.sh`: delete `_up_aliases_read_prefix` entirely, simplify `_up_sync_aliases` to a single `install.sh aliases --no-profile` invocation. Tests: replace one `_up_aliases_read_prefix` test, update one log-message assertion, add 5 new tests across two BATS files.

**Tech Stack:** Bash 5.x, BATS (Bash Automated Testing System).

**Design doc:** `docs/plans/2026-05-10-update-aliases-followup-design.md`

**Branch:** `refactor/update-aliases-followup` (already created from `main`, design doc committed at `d6aa0db`).

**Single PR, single logical commit.** The refactor and the test changes are tightly coupled — splitting would leave intermediate states with deleted helpers and untested new behavior.

---

## Background

Current `bin/lib/update.sh:_up_sync_aliases` does:

1. Check `bin/aliases.sh` exists → skip with INFO if not.
2. Call `_up_aliases_read_prefix` (local helper, 22 lines) to extract the prefix from the file's header (with alias-name fallback).
3. Skip with WARN if prefix is empty.
4. Dry-run? Log and return.
5. Run `install.sh aliases --prefix <prefix>` → which invokes `_aliases_install_source_line` (rewrites the user's `.bashrc`/`.zshrc` line every update).

The duplication: `_up_aliases_read_prefix` reimplements `_aliases_read_existing_prefix` in `bin/install/aliases.sh:199` with subtle differences (the update version has a robust whitespace trim and the alias-name fallback; the install version doesn't).

After this PR:

1. Check `bin/aliases.sh` exists → skip with INFO if not.
2. Dry-run? Log and return.
3. Run `install.sh aliases --no-profile` (no `--prefix`). `_aliases_main` reads the existing prefix via `_aliases_read_existing_prefix` (now with the fallback), regenerates the file, skips the source-line install because `--no-profile`.

Net result: `bin/lib/update.sh` shrinks. `bin/install/aliases.sh` grows by one flag plus a fallback in the prefix reader. Profile is never touched during updates.

---

## Task 1: Enhance `_aliases_read_existing_prefix` in `bin/install/aliases.sh`

**Files:**
- Modify: `bin/install/aliases.sh:199-209`

**Step 1: Replace the function body**

Find the existing function at line 199:

```bash
_aliases_read_existing_prefix() {
    if [[ -f "$ALIASES_FILE" ]]; then
        local line
        line="$(grep '^# Prefix:' "$ALIASES_FILE" 2>/dev/null || true)"
        if [[ -n "$line" ]]; then
            echo "${line#*: }"
            return 0
        fi
    fi
    echo ""
}
```

Replace with:

```bash
_aliases_read_existing_prefix() {
    if [[ -f "$ALIASES_FILE" ]]; then
        local line

        line="$(grep -m1 '^# Prefix:' "$ALIASES_FILE" 2>/dev/null || true)"
        if [[ -n "$line" ]]; then
            # Trim leading/trailing whitespace after the colon.
            line="${line#*:}"
            line="${line#"${line%%[![:space:]]*}"}"
            line="${line%"${line##*[![:space:]]}"}"
            echo "$line"
            return 0
        fi

        # Fallback: extract prefix from the first 'alias <prefix>-check-health=' line.
        line="$(grep -m1 '^alias [^=]*-check-health=' "$ALIASES_FILE" 2>/dev/null || true)"
        if [[ -n "$line" ]]; then
            line="${line#alias }"
            line="${line%%=*}"
            echo "${line%-check-health}"
            return 0
        fi
    fi
    echo ""
}
```

Two changes:
- The header trim uses bash parameter expansion to strip leading/trailing whitespace (handles multiple spaces, tabs).
- A new fallback parses the `alias <prefix>-check-health=` line when the header is missing or corrupt.

**Step 2: Verify syntax**

```bash
bash -n bin/install/aliases.sh
```
Expected: exit 0.

---

## Task 2: Add `--no-profile` flag to `_aliases_main`

**Files:**
- Modify: `bin/install/aliases.sh:215-300`

**Step 1: Add a new local var and a new flag case branch**

Inside `_aliases_main` (line 215), add `local skip_source_line=false` near the other locals (line 217 area). Then add a new case in the arg-parse `while` loop:

```bash
            --no-profile)
                skip_source_line=true
                i=$((i + 1))
                ;;
```

Place it next to the other flag handlers (after `--help|-h`, before the `*)` default).

**Step 2: Guard the source-line install**

In the `setup)` case (around line 297-298), change:

```bash
            info "Using prefix: $prefix"
            _aliases_generate "$prefix"
            export ALIAS_PREFIX="$prefix"
            _aliases_install_source_line
            ;;
```

to:

```bash
            info "Using prefix: $prefix"
            _aliases_generate "$prefix"
            export ALIAS_PREFIX="$prefix"
            [ "$skip_source_line" = false ] && _aliases_install_source_line
            ;;
```

**Step 3: Verify syntax**

```bash
bash -n bin/install/aliases.sh
```
Expected: exit 0.

---

## Task 3: Update `_aliases_show_help` to document `--no-profile`

**Files:**
- Modify: `bin/install/aliases.sh:52-71`

**Step 1: Add the flag to the help text**

Find the Options block:

```
Options:
  --prefix VALUE   Alias prefix (default: sm)
                   Example: --prefix maint  =>  maint-check-health, ...
  --remove         Remove generated aliases and the source line from shell profile
  --list           Show currently generated aliases
  --help           Show this help message
```

Add one line after `--list`:

```
  --no-profile     Regenerate aliases file but skip modifying shell profile
```

So the Options block becomes:

```
Options:
  --prefix VALUE   Alias prefix (default: sm)
                   Example: --prefix maint  =>  maint-check-health, ...
  --remove         Remove generated aliases and the source line from shell profile
  --list           Show currently generated aliases
  --no-profile     Regenerate aliases file but skip modifying shell profile
  --help           Show this help message
```

**Step 2: Verify syntax**

```bash
bash -n bin/install/aliases.sh
```

---

## Task 4: Delete `_up_aliases_read_prefix` and simplify `_up_sync_aliases`

**Files:**
- Modify: `bin/lib/update.sh:268-321`

**Step 1: Read the current implementation to confirm line numbers**

```bash
sed -n '265,325p' bin/lib/update.sh
```

You'll see:
- Lines 268-289: `_up_aliases_read_prefix()` (to delete)
- Lines 290-291: a blank line and the start of `_up_sync_aliases`
- Lines 292-321: `_up_sync_aliases()` (to simplify)

**Step 2: Delete `_up_aliases_read_prefix` entirely**

Remove lines 268-289 (the entire function and its trailing blank line). The function above (`_up_sync_env` close brace at 265) flows directly into `_up_sync_aliases`.

**Step 3: Replace `_up_sync_aliases` body**

Replace the entire `_up_sync_aliases` function (was lines 292-321) with:

```bash
_up_sync_aliases() {
    local aliases_file="$BIN_DIR/aliases.sh"

    if [ ! -f "$aliases_file" ]; then
        _up_log "INFO" "Aliases not configured, skipping aliases sync"
        return 0
    fi

    _up_log "INFO" "Regenerating aliases from install template"

    if [ "$_up_dry_run" = true ]; then
        _up_log "INFO" "Dry-run: would run install.sh aliases --no-profile"
        return 0
    fi

    if ! (cd "$PROJECT_DIR" && "$BIN_DIR/install.sh" aliases --no-profile </dev/null); then
        _up_log "WARN" "Alias regeneration failed; keeping existing aliases"
        return 0
    fi

    _up_log "OK" "Aliases regenerated from install template"
    return 0
}
```

Key changes vs. pre-PR-#143-followup:
- No `_up_aliases_read_prefix` call.
- No `prefix` local variable.
- No `Could not detect alias prefix` WARN path (now handled by `_aliases_main` falling back to the existing-prefix or `sm` default automatically).
- `install.sh aliases --no-profile` (no `--prefix $prefix` arg).
- Dry-run message updated to reflect the new invocation.

**Step 4: Verify syntax**

```bash
bash -n bin/lib/update.sh
```
Expected: exit 0.

---

## Task 5: Update existing test in `test_update.bats`

Two existing tests reference `_up_aliases_read_prefix` or the old log message. Update them.

**Files:**
- Modify: `bin/tests/test_update.bats:42-72`

**Step 1: Delete the prefix-extraction test**

The test "update lib reads alias prefix from generated aliases file" (around line 42) tests `_up_aliases_read_prefix`. That function is gone. Delete the entire test block (the `@test "update lib reads alias prefix from generated aliases file" { ... }` block, including the trailing blank line if any).

Equivalent coverage lands in Task 7 below (testing `_aliases_read_existing_prefix` directly in `test_install.bats`).

**Step 2: Update the dry-run test's assertion**

The test "update lib dry-run sync aliases logs regeneration step" (around line 57) asserts:

```bash
assert_output --partial "Dry-run: would run install.sh aliases --prefix sm"
```

Update to:

```bash
assert_output --partial "Dry-run: would run install.sh aliases --no-profile"
```

The fixture inside that test can stay as-is (the test setup writes a `bin/aliases.sh` with `Prefix: sm`; the new code doesn't care about the prefix because it passes no `--prefix` flag). Only the assertion string changes.

**Step 3: Run the modified test file**

```bash
bin/tests/test_helper/bats-core/bin/bats bin/tests/test_update.bats
```

Expected: all tests pass. (One test was deleted, one assertion was updated; the rest are unchanged.)

---

## Task 6: Add new tests to `test_update.bats`

**Files:**
- Modify: `bin/tests/test_update.bats` (append to the end)

**Step 1: Add two new tests**

Append after the existing tests:

```bash

@test "_up_sync_aliases skips when aliases.sh does not exist" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        temp_bin="$(mktemp -d)"
        BIN_DIR="$temp_bin"
        PROJECT_DIR="$(dirname "$temp_bin")"
        _up_dry_run=false
        _up_json_mode=false
        _up_sync_aliases
    '
    assert_success
    assert_output --partial "Aliases not configured"
}

@test "_up_sync_aliases logs WARN and returns 0 when install.sh fails" {
    run bash -c '
        source "'"$LIB_DIR/update.sh"'"
        temp_bin="$(mktemp -d)"
        BIN_DIR="$temp_bin"
        PROJECT_DIR="$(dirname "$temp_bin")"

        # Provide a fake aliases.sh so the early-return is skipped.
        cat > "$BIN_DIR/aliases.sh" <<EOF
# Prefix: sm
alias sm-check-health='\''cd "/tmp" && true'\''
EOF

        # Fake install.sh that exits 1.
        cat > "$BIN_DIR/install.sh" <<EOF
#!/usr/bin/env bash
exit 1
EOF
        chmod +x "$BIN_DIR/install.sh"

        _up_dry_run=false
        _up_json_mode=false
        _up_sync_aliases
    '
    assert_success
    assert_output --partial "Alias regeneration failed"
}
```

**Step 2: Run the new tests**

```bash
bin/tests/test_helper/bats-core/bin/bats bin/tests/test_update.bats
```

Expected: all tests pass. The two new tests verify the skip-on-missing-file and regen-failure code paths.

---

## Task 7: Add tests to `test_install.bats` for the prefix reader and `--no-profile` flag

**Files:**
- Modify: `bin/tests/test_install.bats` (append to the end)

The existing `test_install.bats` is mostly syntax checks. We'll add 5 new tests that source the aliases module and exercise its functions.

A complication: `bin/install/aliases.sh` calls `_aliases_main "$@"` at module level. Sourcing it from a test would execute the main flow with whatever args the test provides — potentially modifying the user's actual shell profile. We need to source carefully.

**Strategy:** spawn a subshell, override `HOME` to a temp dir so any profile edits land safely, set `BIN_DIR` and `_INSTALL_DIR` so the module knows where to write, then source the module. The `_aliases_main` call at module level will dispatch based on the args we pass.

For the read-existing-prefix tests, we'll source the module with `--help` (a no-op) to define the functions without triggering generate or remove, then call `_aliases_read_existing_prefix` directly.

**Step 1: Add the 5 tests**

Append after the existing tests:

```bash

@test "_aliases_read_existing_prefix returns prefix from # Prefix: header" {
    run bash -c '
        export HOME="$(mktemp -d)"
        export BIN_DIR="$(mktemp -d)"
        export PROJECT_DIR="$(dirname "$BIN_DIR")"
        cat > "$BIN_DIR/aliases.sh" <<EOF
# Prefix: maint
alias maint-check-health='\''cd "/tmp" && true'\''
EOF
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output "maint"
}

@test "_aliases_read_existing_prefix falls back to alias-name parsing when header missing" {
    run bash -c '
        export HOME="$(mktemp -d)"
        export BIN_DIR="$(mktemp -d)"
        export PROJECT_DIR="$(dirname "$BIN_DIR")"
        cat > "$BIN_DIR/aliases.sh" <<EOF
# No prefix header here
alias custom-check-health='\''cd "/tmp" && true'\''
alias custom-run-check='\''cd "/tmp" && true'\''
EOF
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output "custom"
}

@test "_aliases_read_existing_prefix returns empty when both methods fail" {
    run bash -c '
        export HOME="$(mktemp -d)"
        export BIN_DIR="$(mktemp -d)"
        export PROJECT_DIR="$(dirname "$BIN_DIR")"
        cat > "$BIN_DIR/aliases.sh" <<EOF
# Some other file with no header and no -check-health alias
alias something-else='\''cd "/tmp" && true'\''
EOF
        source "'"$BIN_DIR_REAL/install/aliases.sh"'" --help >/dev/null
        ALIASES_FILE="$BIN_DIR/aliases.sh"
        _aliases_read_existing_prefix
    '
    assert_success
    assert_output ""
}

@test "install.sh aliases --no-profile regenerates aliases without modifying profile" {
    run bash -c '
        export HOME="$(mktemp -d)"
        : > "$HOME/.bashrc"
        # Use the real BIN_DIR but a clean ALIASES_FILE target.
        export TEST_BIN="$(mktemp -d)"
        export BIN_DIR="$TEST_BIN"
        export PROJECT_DIR="$(dirname "$TEST_BIN")"
        mkdir -p "$TEST_BIN/install" "$TEST_BIN/lib"
        # Copy the real lib helpers + aliases module so the SCRIPT can source them.
        cp -r "'"$BIN_DIR_REAL/lib"'/." "$TEST_BIN/lib/"
        cp "'"$BIN_DIR_REAL/install/aliases.sh"'" "$TEST_BIN/install/aliases.sh"
        # Invoke the module directly with --no-profile and a known prefix.
        bash "$TEST_BIN/install/aliases.sh" --prefix sm --no-profile >/dev/null 2>&1
        # Aliases file was written...
        [ -f "$TEST_BIN/aliases.sh" ]
        # ...but the profile was NOT touched.
        [ ! -s "$HOME/.bashrc" ]
    '
    assert_success
}

@test "install.sh aliases --prefix without --no-profile DOES modify profile" {
    run bash -c '
        export HOME="$(mktemp -d)"
        export SHELL=/bin/bash
        : > "$HOME/.bashrc"
        export TEST_BIN="$(mktemp -d)"
        export BIN_DIR="$TEST_BIN"
        export PROJECT_DIR="$(dirname "$TEST_BIN")"
        mkdir -p "$TEST_BIN/install" "$TEST_BIN/lib"
        cp -r "'"$BIN_DIR_REAL/lib"'/." "$TEST_BIN/lib/"
        cp "'"$BIN_DIR_REAL/install/aliases.sh"'" "$TEST_BIN/install/aliases.sh"
        bash "$TEST_BIN/install/aliases.sh" --prefix sm >/dev/null 2>&1
        [ -f "$TEST_BIN/aliases.sh" ]
        # Source line WAS added to the (otherwise empty) profile.
        grep -qF "server-maintanence aliases" "$HOME/.bashrc"
    '
    assert_success
}
```

**Note about `$BIN_DIR_REAL`:** the inner bash subshells reset `BIN_DIR` to a temp dir, so they can't reach the real `bin/lib` or `bin/install`. Capture the real `BIN_DIR` *before* the subshell:

At the top of each test method, before the `run bash -c '...'` line, add:
```bash
local BIN_DIR_REAL="$BIN_DIR"
export BIN_DIR_REAL
```

Then inside the heredoc, reference `'"$BIN_DIR_REAL/lib"'` etc. (already shown above).

If the heredoc-escape gymnastics get unmanageable, fall back to writing the test body to a temp script file and invoking it — or use `bats`'s `setup_file` to compute paths once. Pick whichever form actually works on your BATS version.

**Step 2: Run the new tests**

```bash
bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats
```

Expected: all tests pass. If the heredoc escaping is brittle, simplify the test bodies — the assertions are the load-bearing part.

---

## Task 8: Verify, lint, manual sanity check

**Step 1: Full BATS suite**

```bash
for f in bin/tests/*.bats; do
    echo "=== $f ==="
    bin/tests/test_helper/bats-core/bin/bats "$f" || echo "FAILED: $f"
done
```

Expected: every `*.bats` file passes. The other test files (`test_cli.bats`, `test_set_production.bats`) shouldn't be affected.

**Step 2: Python suite (sanity — no Python files touched)**

```bash
uv run pytest apps/ 2>&1 | tail -3
```

Expected: baseline test count passing, no regressions.

**Step 3: Lint, format, type-check**

```bash
uv run black --check apps/ 2>&1 | tail -3
uv run ruff check apps/ 2>&1 | tail -3
uv run mypy apps/ 2>&1 | tail -3
```

Expected: clean. (No Python files touched, but pre-commit hooks will run.)

**Step 4: Manual sanity check**

Run on this Mac (without modifying the local `bin/aliases.sh` or `.zshrc`):

```bash
# Verify the --no-profile flag works on the real install module.
TMP_HOME="$(mktemp -d)"
HOME="$TMP_HOME" SHELL=/bin/bash bin/install.sh aliases --prefix sm --no-profile
# Check that aliases.sh got rewritten:
ls -la bin/aliases.sh
# Check that $TMP_HOME has no .bashrc:
ls "$TMP_HOME/.bashrc" 2>&1 | head -1
# (Should say "No such file" — flag worked.)
rm -rf "$TMP_HOME"

# Verify the update path is dry-run-safe.
bin/update.sh --dry-run 2>&1 | grep -i "alias"
# Expected: "Dry-run: would run install.sh aliases --no-profile" appears.
```

If anything looks wrong, STOP and report.

---

## Task 9: Commit

```bash
git add bin/install/aliases.sh bin/lib/update.sh bin/tests/test_install.bats bin/tests/test_update.bats
git commit -m "$(cat <<'EOF'
refactor(cli): consolidate alias prefix detection; add --no-profile

PR #143 (closing #142) added _up_sync_aliases for the update flow but
duplicated prefix-detection logic and rewrote the user's shell profile
on every update.

Consolidate:
  - Move the alias-name fallback into _aliases_read_existing_prefix
    (canonical location in bin/install/aliases.sh).
  - Delete _up_aliases_read_prefix from bin/lib/update.sh.
  - Add --no-profile flag to _aliases_main; skip
    _aliases_install_source_line when set.
  - Simplify _up_sync_aliases to a single
    `install.sh aliases --no-profile` invocation. The install module
    handles prefix detection automatically.

Net: bin/lib/update.sh shrinks (~22 lines deleted); bin/install/aliases.sh
gains the fallback + flag (~12 lines). User's shell profile is never
touched during updates.

Tests:
  - Delete the now-obsolete _up_aliases_read_prefix test.
  - Update the dry-run test's expected log message
    (--prefix sm -> --no-profile).
  - Add 2 tests for _up_sync_aliases skip-on-missing-file and
    regen-failure paths.
  - Add 5 tests for _aliases_read_existing_prefix (header, fallback,
    empty) and --no-profile (profile NOT modified vs. IS modified).

Backwards compatible: existing `install.sh aliases --prefix sm`
invocations behave identically (profile is still updated). The
--no-profile flag is additive.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass (no Python files touched).

---

## Task 10: Push and open PR

```bash
git push -u origin refactor/update-aliases-followup
gh pr create --base main --title "refactor(cli): consolidate alias prefix detection; add --no-profile" --body "$(cat <<'EOF'
## Summary
Follow-up to PR #143 (which closed #142). Three audit findings addressed:

1. **Duplicate prefix-detection logic**: `_up_aliases_read_prefix` in `bin/lib/update.sh` reimplemented `_aliases_read_existing_prefix` from `bin/install/aliases.sh` with subtle differences. Consolidated into the canonical location with the fallback intact.
2. **Shell-profile side effect on every update**: `_up_sync_aliases` called `install.sh aliases --prefix <prefix>` which invoked `_aliases_install_source_line` — rewriting `.bashrc`/`.zshrc` on every update. Added a `--no-profile` flag to suppress the source-line install.
3. **Test coverage gaps**: only 2 of 5 code paths in `_up_sync_aliases` were tested. Added BATS tests for the missing paths (skip-on-missing-file, regen-failure) and for the new `_aliases_read_existing_prefix` fallback and `--no-profile` flag.

Design doc: `docs/plans/2026-05-10-update-aliases-followup-design.md`

## Changes
- **`bin/install/aliases.sh`**: enhance `_aliases_read_existing_prefix` with alias-name fallback; add `--no-profile` flag to `_aliases_main`; update help text.
- **`bin/lib/update.sh`**: delete `_up_aliases_read_prefix` (~22 lines); simplify `_up_sync_aliases` to a single `install.sh aliases --no-profile` invocation.
- **`bin/tests/test_update.bats`**: delete now-obsolete prefix-extraction test; update one log-message assertion; add 2 new tests for the skip and regen-failure paths.
- **`bin/tests/test_install.bats`**: add 5 new tests covering prefix detection (header, fallback, empty) and `--no-profile` (profile NOT modified vs. IS modified).

## Behavior changes
- **Shell profile is no longer modified during `bin/update.sh`**. The source line is written by `bin/install.sh aliases` (interactive flow); subsequent updates just regenerate the aliases file via `--no-profile`.
- **`install.sh aliases --no-profile`** is a new public flag. Documented in `_aliases_show_help`. Useful for operators who want to regen aliases without touching their profile.
- **Existing `install.sh aliases --prefix sm`** invocations behave identically (profile is still updated). The flag is additive; no breaking change.

## Test plan
- [x] `bash -n` clean on modified files.
- [x] All BATS tests pass (`bin/tests/*.bats`).
- [x] `uv run pytest apps/` — baseline test count passes (no Python files touched).
- [x] Manual: `bin/install.sh aliases --prefix sm --no-profile` with `HOME` in a temp dir regenerates `bin/aliases.sh` and does NOT create or modify the temp `.bashrc`.
- [x] Manual: `bin/update.sh --dry-run` logs `"Dry-run: would run install.sh aliases --no-profile"`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Single PR, single commit**. The refactor (delete-helper + simplify + add-flag) and the test changes are tightly coupled. Don't split — intermediate states would have deleted helpers and untested new behavior.
- **`bin/aliases.sh` is gitignored** — do not commit any locally-regenerated copy.
- **Match existing log style**. `_up_log "INFO"`, `_up_log "OK"`, `_up_log "WARN"` in `bin/lib/update.sh`. `info`/`success`/`warn`/`error` in `bin/install/aliases.sh` (from `lib/logging.sh`).
- **BATS heredoc escaping** is fiddly. The escape pattern `'\''` inside `bash -c '...'` heredocs is correct but easy to get wrong. If escapes get unmanageable, write the test body to a temp script file and `bash $script` instead.
- **`HOME` must be a temp dir in tests that exercise profile modification**. Never leave `HOME` as the user's real home — even with idempotent writes, you don't want your CI to touch `~/.bashrc`.
- **`SHELL` env var** drives `_aliases_detect_profile`'s choice of `.bashrc` vs `.zshrc`. Setting `SHELL=/bin/bash` in tests makes the profile target predictable.
- **The `setup)` case in `_aliases_main`** is where `_aliases_generate` and `_aliases_install_source_line` are called. The `--no-profile` flag's only effect is guarding the second call. The `setup` action is the default; `--remove`, `--list`, `--help` are unaffected.
- **`_aliases_show_help`** is at line 52. The new `--no-profile` line goes alphabetically near the bottom of the Options block (after `--list`).
- **Don't touch `bin/install.sh`** — it just forwards to `bin/install/aliases.sh` and doesn't need to know about the new flag.