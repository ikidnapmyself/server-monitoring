---
title: "Update Aliases Follow-up: Remove Duplication, Cover Skip Paths Design"
parent: Plans
---

# Update Aliases Follow-up: Remove Duplication, Cover Skip Paths Design

## Problem

PR #143 (closed issue #142) added `_up_sync_aliases` to regenerate `bin/aliases.sh` during `bin/update.sh`. The implementation works and passes the issue's acceptance criteria, but a follow-up audit found three issues:

1. **Duplicate prefix-detection.** `bin/lib/update.sh:_up_aliases_read_prefix` (lines 268-289) reimplements the same logic as `bin/install/aliases.sh:_aliases_read_existing_prefix` (line 199), with subtle differences:
   - `_up_aliases_read_prefix` parses the `# Prefix:` header with a robust whitespace-trim and adds an alias-name-parse fallback (`alias <prefix>-check-health=...`).
   - `_aliases_read_existing_prefix` parses the same header with a single-space assumption and has no fallback.
   `_aliases_main` in the install module already calls `_aliases_read_existing_prefix` when `--prefix` is omitted (lines 277-286), so the update path doesn't need its own prefix reader.

2. **Shell-profile side effect on every update.** `_up_sync_aliases` calls `bin/install.sh aliases --prefix <prefix>`, which goes through `_aliases_main` → `_aliases_install_source_line`. That function rewrites the marker line in `.bashrc`/`.zshrc` on every update. Idempotent (replaces with identical content when path is unchanged), but the file gets touched on disk, changing mtime and triggering backup/editor "file changed externally" alerts.

3. **Test coverage gaps.** Five code paths through `_up_sync_aliases` exist; only 2 are tested (primary prefix detection + dry-run). The fallback prefix detection (`alias <prefix>-check-health` parse), the two skip paths (`aliases.sh` missing; prefix cannot be detected), and the regen-failure path are all untested.

The audit was filed against the merged state; this PR addresses items 1-3.

## Scope

In scope:
- Merge the prefix-detection logic into the single canonical location (`_aliases_read_existing_prefix` in `bin/install/aliases.sh`), gaining the alias-name fallback there.
- Delete `_up_aliases_read_prefix` from `bin/lib/update.sh` (no longer needed).
- Add a `--no-profile` flag to `_aliases_main` that suppresses `_aliases_install_source_line`. Documented in `_aliases_show_help`.
- Simplify `_up_sync_aliases` to call `bin/install.sh aliases --no-profile` — no manual prefix detection, no `--prefix` argument, no `_up_aliases_read_prefix` call. The install module handles prefix detection (now with the robust fallback) on its own.
- Add tests for the missing paths: alias-name fallback in `_aliases_read_existing_prefix`; empty-return when both methods fail; `--no-profile` skips source-line install; update step skip-on-missing-file; update step regen-failure path.

Out of scope:
- Step placement in `run_update` (audit item 3 — defensible as-is).
- Heredoc / copilot firewall note (audit item 5 — operational).
- Changes to other update steps (`_up_sync_env`, `_up_sync_deps`, `_up_migrate`, `_up_restart`).
- Changes to other Django commands, BATS infrastructure, or CI.

## Approach

The user's "don't repeat ourselves" direction makes the single-public-entry-point approach the right choice. Three approaches considered during brainstorming:

- **A**: Add a `--no-profile` flag and delete `_up_aliases_read_prefix`. ← chosen.
- **B**: Extract `_aliases_generate` to a separate `bin/lib/aliases.sh` library. Cleaner long-term separation but bigger refactor for one consumer.
- **C**: Restructure the template to not auto-run `_aliases_main` on source. Subtle blast radius in `install.sh`.

**A** wins because the `install.sh aliases` entry already does almost everything (`_aliases_main` reads the existing prefix, falls back interactively or via the existing prefix when not given, calls `_aliases_generate` to regenerate, then optionally calls `_aliases_install_source_line`). The only missing piece for the update use case is "skip the source-line install" — a one-flag addition.

### `bin/install/aliases.sh` changes

1. **Enhance `_aliases_read_existing_prefix`** (line 199). Current implementation only parses the `# Prefix:` header. Add a fallback that parses an `alias <prefix>-check-health=` line when the header is missing/corrupt:

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

   The robust trim replaces the existing `${line#*: }` (which assumed a single space after the colon). The fallback mirrors the logic from the now-deleted `_up_aliases_read_prefix`.

2. **Add `--no-profile` flag to `_aliases_main`** (line 215). New local variable in the arg-parse loop, new case branch, new guard around `_aliases_install_source_line`:

   ```bash
   _aliases_main() {
       local prefix=""
       local action="setup"
       local skip_source_line=false

       local -a args=("$@")
       local i=0
       while [[ $i -lt ${#args[@]} ]]; do
           case "${args[$i]}" in
               --prefix)
                   ...
                   ;;
               --remove) action="remove"; i=$((i + 1)) ;;
               --list) action="list"; i=$((i + 1)) ;;
               --help|-h) action="help"; i=$((i + 1)) ;;
               --no-profile) skip_source_line=true; i=$((i + 1)) ;;
               *)
                   ...
                   ;;
           esac
       done

       case "$action" in
           ...
           setup)
               ...
               info "Using prefix: $prefix"
               _aliases_generate "$prefix"
               export ALIAS_PREFIX="$prefix"
               [ "$skip_source_line" = false ] && _aliases_install_source_line
               ;;
       esac
   }
   ```

3. **Update `_aliases_show_help`** to document the new flag.

### `bin/lib/update.sh` changes

4. **Delete `_up_aliases_read_prefix`** (lines 268-289). ~22 lines gone.

5. **Simplify `_up_sync_aliases`** (lines 292-321):

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

   Net: the function shrinks from ~30 lines to ~20. No more prefix detection, no more `--prefix` argument to construct. `install.sh aliases` (via `_aliases_main` → `_aliases_read_existing_prefix`) handles prefix discovery on its own.

## Edge cases

- **Missing `bin/aliases.sh`** — `_up_sync_aliases` short-circuits with an INFO log; never invokes `install.sh`. Same behavior as before.
- **Corrupted `# Prefix:` header** — the new fallback in `_aliases_read_existing_prefix` parses the `alias <prefix>-check-health=` line. If both methods fail, returns empty. Then `_aliases_main` line 278 falls back to the hardcoded `"sm"` default — same as today.
- **Non-interactive stdin** (`bin/update.sh` always runs non-interactively from cron or scripts). `_aliases_main` line 280-286 detects `[[ -t 0 ]]` is false and uses the fallback prefix without prompting. The `</dev/null` redirect in `_up_sync_aliases` guarantees stdin is non-interactive.
- **`install.sh aliases --no-profile` invoked manually by an operator**. The flag is documented in `_aliases_show_help`; it's a valid use case for someone who wants the file regenerated without touching their profile.
- **Dry-run mode** — logs the intended `install.sh aliases --no-profile` invocation and returns. No filesystem changes.
- **Concurrent runs** — `install.sh aliases` uses `cat > $ALIASES_FILE <<...`, which is atomic-ish on POSIX filesystems (the file is created with the full content). No locking, but the regen is small enough that a race is improbable.
- **First-ever invocation when `aliases.sh` doesn't exist yet** — `_up_sync_aliases` skips. User runs `bin/install.sh aliases` manually first. After that, every subsequent update keeps the file in sync.
- **User removed the source line manually** from their profile (without `--remove`) — every update would re-add it under the current implementation. After this PR, `--no-profile` means the profile is *never* touched by the update path, even if the user wants the source line. Existing source line in the profile keeps pointing at the (now-rewritten) `bin/aliases.sh`, so things continue to work. This is the correct behavior — update should never modify the user's shell profile.
- **`_aliases_install_source_line` is still invoked by `bin/install.sh aliases` (the interactive flow)** — users running `bin/install.sh aliases` manually still get their profile updated. Only the update path skips the profile manipulation. The split is correct.

## Testing

### Tests for `bin/install/aliases.sh`

Add to `bin/tests/test_install.bats` (or create `bin/tests/test_install_aliases.bats` if you prefer separation). Five tests:

1. **`_aliases_read_existing_prefix returns prefix from # Prefix: header`** — fixture file with `# Prefix: maint`, function returns `"maint"`.
2. **`_aliases_read_existing_prefix falls back to alias-name parsing when header missing`** — fixture file with NO `# Prefix:` line, but with `alias custom-check-health='...'`. Function returns `"custom"`.
3. **`_aliases_read_existing_prefix returns empty when both methods fail`** — fixture file with no header and no `-check-health` alias. Function returns empty string.
4. **`_aliases_main --no-profile regenerates aliases but does not modify profile`** — fixture: a temp `HOME` with empty `.bashrc`. Run `_aliases_main --prefix sm --no-profile`. Assert `aliases.sh` was created. Assert `.bashrc` is unchanged (still empty).
5. **`_aliases_main --prefix sm (without --no-profile) DOES modify profile`** — sanity check: same fixture, without the flag, `.bashrc` now contains the source line. Locks the default behavior.

### Tests for `bin/lib/update.sh`

Update `bin/tests/test_update.bats`:

1. **Delete the existing "update lib reads alias prefix from generated aliases file" test** — function it tests (`_up_aliases_read_prefix`) is gone. The equivalent behavior is tested in test_install.bats (test 1 above).
2. **Keep the dry-run test, update the expected log message** — was `"Dry-run: would run install.sh aliases --prefix sm"`, becomes `"Dry-run: would run install.sh aliases --no-profile"` (no prefix argument any more).
3. **Add: skip path when `bin/aliases.sh` doesn't exist** — temp `BIN_DIR` with no `aliases.sh`. Run `_up_sync_aliases`. Assert exit 0 and `--partial "Aliases not configured"` log.
4. **Add: regen failure path** — temp `BIN_DIR` with an `aliases.sh` and a fake `install.sh` that exits 1. Run `_up_sync_aliases`. Assert exit 0 (best-effort) and `--partial "Alias regeneration failed"` log.

### Coverage

After the changes, every branch of `_up_sync_aliases` is exercised:
- File missing → tested (new test 3 above).
- Dry-run → tested (existing test).
- Regen failure → tested (new test 4 above).
- Regen success — not directly tested in BATS (would require a fully working `install.sh` setup), but the dry-run + skip + failure paths cover everything except the happy path's command output. Acceptable for a smoke test.

Every branch of `_aliases_read_existing_prefix` is exercised:
- Header present → tested (test 1).
- Header missing, alias fallback present → tested (test 2).
- Both missing → tested (test 3).

`--no-profile` flag is covered both positively (test 4: profile NOT modified) and negatively (test 5: profile IS modified without the flag).

## Notes for implementation

- **Single PR, one logical commit**. The refactor and the test additions are tightly coupled — splitting would leave intermediate states with deleted helpers and untested new behavior.
- **`bin/aliases.sh` is gitignored** — don't commit a regenerated local copy. The template (`bin/install/aliases.sh`) is what gets committed.
- **Match existing log style**. `_up_log "INFO" ...`, `_up_log "OK" ...`, `_up_log "WARN" ...` are the patterns in `bin/lib/update.sh`. The `info`/`success`/`warn`/`error` functions in `bin/install/aliases.sh` come from `lib/logging.sh`. Use the right helper for each file.
- **No interactive prompts in the update path**. The `</dev/null` redirect on the `install.sh aliases` call already prevents prompts, but `_aliases_main`'s `[[ -t 0 ]]` guard at line 280 also detects non-interactive stdin and uses the fallback prefix automatically. Both defenses in place.
- **`_aliases_show_help`** is in the same file (around line 52). Add a single line for `--no-profile`:
  ```
  #   --no-profile      Regenerate aliases file but do not modify shell profile
  ```
- **Don't change `bin/install.sh`** itself — the only changes are in the sourced `bin/install/aliases.sh` module.
- **Backward compatibility for `install.sh aliases`**: existing invocations (no `--no-profile`) keep the old behavior (profile modified). The flag is additive; no breaking change for any caller.