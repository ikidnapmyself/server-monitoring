---
title: "tuin as the bin/cli.sh UI — Implementation Plan"
parent: Plans
---

# tuin CLI UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the `select`/`read` interactive UI in `bin/cli.sh` and its five Django-wrapper submenus with vendored tuin v0.3.0 primitives, adding arrow-key pickers for single-checker / single-pipeline-definition / notify-driver flows.

**Architecture:** Vendor tuin as a committed single file that rides the existing `git pull` update path. Convert menus to `tuin_menu`/`tuin_confirm`/`tuin_input`. Add a unit-testable `bin/lib/pickers.sh` that parses existing `manage.py` list output in shell (no Django changes). Land in 5 phases, each leaving the CLI fully working.

**Tech Stack:** Bash 3.2+ (macOS default — **no `mapfile`/`readarray`**), tuin v0.3.0, bats (bats-core + bats-support + bats-assert), shellcheck (manual).

**Design doc:** `docs/plans/2026-06-02-tuin-cli-ui-design.md`

---

## Conventions for the executor

- Work on the current branch `design/tuin-cli-ui` (do NOT use a worktree; do NOT push to `main`).
- Run the full bats suite with: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
- After touching any shell file, run: `shellcheck bin/cli.sh bin/cli/*.sh bin/lib/tuin_vendor.sh bin/lib/pickers.sh` (never shellcheck the vendored `bin/lib/tuin.sh`).
- **Bash 3.2 rule:** read command output into arrays with a `while IFS= read -r` loop, never `mapfile`/`readarray`.
- Keep `tuin_menu`/`tuin_confirm`/`tuin_choose` calls in `if`/`while` condition position (safe under `set -e`).
- Capture `tuin_input`/`tuin_choose` values with `$(…)`.

---

## Phase 0 — Vendor tuin (inert: no UI change)

### Task 0.1: Vendor the pinned tuin.sh

**Files:**
- Create: `bin/lib/tuin.sh`

**Step 1: Download the pinned release**

Run:
```bash
curl -fsSL https://raw.githubusercontent.com/ikidnapmyself/tuin/v0.3.0/tuin.sh -o bin/lib/tuin.sh
```

**Step 2: Verify it loads and reports the right version**

Run:
```bash
bash -c 'source bin/lib/tuin.sh; tuin_version'
```
Expected: prints a version line mentioning `0.3.0`.

**Step 3: Commit**

```bash
git add bin/lib/tuin.sh
git commit -m "feat(cli): vendor tuin v0.3.0 (single-file pure-bash TUI)"
```

---

### Task 0.2: Add the re-vendor helper

**Files:**
- Create: `bin/lib/tuin_vendor.sh`

**Step 1: Write the helper**

```bash
#!/usr/bin/env bash
#
# Vendoring helper for tuin (single-file pure-bash TUI library).
# Source this file — do not execute directly.
#
[[ -n "${_LIB_TUIN_VENDOR_LOADED:-}" ]] && return 0
_LIB_TUIN_VENDOR_LOADED=1

_TV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Bump this one line to update tuin, then run `vendor_tuin` and commit.
TUIN_VERSION="${TUIN_VERSION:-v0.3.0}"
TUIN_LOCAL="$_TV_DIR/tuin.sh"
TUIN_URL="https://raw.githubusercontent.com/ikidnapmyself/tuin/${TUIN_VERSION}/tuin.sh"

# vendor_tuin — (re)download the pinned tuin.sh into bin/lib/tuin.sh
vendor_tuin() {
    command -v curl >/dev/null 2>&1 || { echo "curl is required to vendor tuin" >&2; return 1; }
    echo "Fetching tuin ${TUIN_VERSION} -> ${TUIN_LOCAL}" >&2
    curl -fsSL "$TUIN_URL" -o "$TUIN_LOCAL" || { echo "Failed to fetch tuin" >&2; return 1; }
    echo "tuin ${TUIN_VERSION} vendored." >&2
}

# ensure_tuin — fetch only if missing (self-heal for installs)
ensure_tuin() {
    [[ -f "$TUIN_LOCAL" ]] || vendor_tuin
}
```

**Step 2: Verify shellcheck is clean**

Run: `shellcheck bin/lib/tuin_vendor.sh`
Expected: no output (exit 0).

**Step 3: Verify ensure_tuin is a no-op when the file exists**

Run:
```bash
bash -c 'source bin/lib/tuin_vendor.sh; ensure_tuin; echo OK'
```
Expected: prints `OK` with no fetch (file already present).

**Step 4: Commit**

```bash
git add bin/lib/tuin_vendor.sh
git commit -m "feat(cli): add tuin vendoring helper (vendor_tuin/ensure_tuin)"
```

---

### Task 0.3: Self-heal in install.sh

**Files:**
- Modify: `bin/install.sh:25-29` (lib source block)

**Step 1: Add the source + ensure_tuin line**

After the existing `source "$SCRIPT_DIR/lib/profile.sh"` line (currently line 29), add:
```bash
source "$SCRIPT_DIR/lib/tuin_vendor.sh"
ensure_tuin
```

**Step 2: Verify install.sh still parses**

Run: `bash -n bin/install.sh`
Expected: no output (exit 0).

**Step 3: Run the install bats suite**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats`
Expected: all pass.

**Step 4: Commit**

```bash
git add bin/install.sh
git commit -m "feat(cli): self-heal vendored tuin via ensure_tuin in install.sh"
```

---

## Phase 1 — Core helpers + main menu

### Task 1.1: Source tuin and convert show_banner / confirm_and_run

**Files:**
- Modify: `bin/cli.sh:22-24` (source block), `bin/cli.sh:33-44` (`show_banner`), `bin/cli.sh:64-80` (`confirm_and_run`)

**Step 1: Add tuin to the source block**

After `source "$SCRIPT_DIR/lib/paths.sh"` (line 24) add:
```bash
source "$SCRIPT_DIR/lib/tuin.sh"
source "$SCRIPT_DIR/lib/pickers.sh"
```
> Note: `pickers.sh` is created in Phase 3. To keep Phase 1 runnable, create a one-line placeholder now: `printf '%s\n' '# Sourced by cli.sh — picker helpers (populated in Phase 3).' > bin/lib/pickers.sh` and `git add` it in this task's commit.

**Step 2: Convert show_banner**

Replace the body of `show_banner` with:
```bash
show_banner() {
    clear
    tuin_banner "Server Maintenance CLI"
    if [ ! -f "$SCRIPT_DIR/aliases.sh" ]; then
        echo -e "${YELLOW}Tip:${NC} Run ${CYAN}bin/install.sh aliases${NC} for quick command aliases (sm-check-health, sm-run-check, etc.)"
        echo ""
    fi
}
```

**Step 3: Convert confirm_and_run**

Replace the body of `confirm_and_run` with:
```bash
confirm_and_run() {
    local cmd="$1"
    tuin_section "Command to run"
    echo -e "  ${CYAN}${cmd}${NC}"
    if tuin_confirm "Run this command?" n; then
        eval "$cmd"
        return $?
    else
        echo -e "${YELLOW}Command cancelled${NC}"
        return 1
    fi
}
```

**Step 4: Verify syntax + shellcheck**

Run: `bash -n bin/cli.sh && shellcheck bin/cli.sh`
Expected: no output.

**Step 5: Smoke the banner non-interactively**

Run: `bash -c 'source bin/lib/tuin.sh; tuin_banner "Server Maintenance CLI"'`
Expected: a boxed banner prints.

**Step 6: Commit**

```bash
git add bin/cli.sh bin/lib/pickers.sh
git commit -m "feat(cli): source tuin; convert banner + confirm_and_run"
```

---

### Task 1.2: Replace the main menu with a tuin loop

**Files:**
- Modify: `bin/cli.sh:115-144` (delete `show_main_menu`), `bin/cli.sh:150-212` (`main()`)

**Step 1: Delete `show_main_menu` and add `main_menu_loop`**

Remove the entire `show_main_menu()` function. In its place add:
```bash
main_menu_loop() {
    local TUIN_MENU_BACK="Exit"
    while true; do
        show_banner
        if tuin_menu "Select an option" \
            "Install / Setup" "Health" "Pipeline" "Intelligence" \
            "Notifications" "Cluster" "Updates"
        then
            case $TUIN_REPLY in
                "Install / Setup") install_project ;;
                "Health")          health_menu ;;
                "Pipeline")        pipeline_menu ;;
                "Intelligence")    intelligence_menu ;;
                "Notifications")   notify_menu ;;
                "Cluster")         cluster_menu ;;
                "Updates")         update_menu ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            echo -e "${GREEN}Goodbye!${NC}"
            return 0
        fi
    done
}
```

**Step 2: Replace the `""` case in main()**

In `main()`, replace the whole `"")` busy-loop block (the `while true; do show_banner; show_main_menu; … done`) with:
```bash
        "")
            main_menu_loop
            ;;
```
Leave the jump-command cases (`health`, `pipeline`, …) as-is — each `*_menu` will loop internally after Phase 2.

**Step 3: Verify syntax + shellcheck**

Run: `bash -n bin/cli.sh && shellcheck bin/cli.sh`
Expected: no output.

**Step 4: Drive the main menu non-interactively**

Run:
```bash
printf '8\n' | bash bin/cli.sh 2>&1 | tail -5
```
Expected: the menu renders (numbered fallback) and selecting the Exit entry prints `Goodbye!` (note: with 7 options + appended Exit/Back, Exit is index 8). Adjust the piped number if tuin numbers Back differently — confirm by reading the fallback list it prints.

**Step 5: Run the cli bats suite**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/test_cli.bats`
Expected: all pass (syntax + `--help` tests unaffected).

**Step 6: Commit**

```bash
git add bin/cli.sh
git commit -m "feat(cli): replace select main menu with tuin_menu loop"
```

---

## Phase 2 — Convert submenus (one task each, no pickers yet)

Each submenu follows the same shape: wrap in `while true`, `if tuin_menu …; then case $TUIN_REPLY …`, drop the explicit "Back to main menu" option, `else return 0`, and add a "Press Enter to continue" pause after each action.

### Task 2.1: Convert health.sh (free-text only; picker added in Phase 3)

**Files:**
- Modify: `bin/cli/health.sh` (full rewrite)

**Step 1: Rewrite the file**

```bash
# Sourced by cli.sh — do not execute directly.

health_menu() {
    while true; do
        show_banner
        if tuin_menu "Health" \
            "Run all health checks" \
            "Run specific checkers" \
            "Run a single checker" \
            "List available checkers" \
            "Preflight dashboard" \
            "JSON output (all checks)" \
            "CI mode: fail on warning" \
            "CI mode: fail on critical only"
        then
            case $TUIN_REPLY in
                "Run all health checks")
                    confirm_and_run "uv run python manage.py check_health" ;;
                "Run specific checkers")
                    run_command "uv run python manage.py check_health --list" "Listing checkers"
                    names=$(tuin_input "Enter checker names (space-separated)")
                    if [ -n "$names" ]; then
                        confirm_and_run "uv run python manage.py check_health $names"
                    else
                        echo -e "${RED}No checkers specified${NC}"
                    fi ;;
                "Run a single checker")
                    run_command "uv run python manage.py check_health --list" "Available checkers"
                    name=$(tuin_input "Enter checker name")
                    if [ -n "$name" ]; then
                        confirm_and_run "uv run python manage.py run_check $name"
                    else
                        echo -e "${RED}Checker name required${NC}"
                    fi ;;
                "List available checkers")
                    run_command "uv run python manage.py check_health --list" "Available checkers" ;;
                "Preflight dashboard")
                    confirm_and_run "uv run python manage.py preflight" ;;
                "JSON output (all checks)")
                    confirm_and_run "uv run python manage.py check_health --json" ;;
                "CI mode: fail on warning")
                    confirm_and_run "uv run python manage.py check_health --fail-on-warning" ;;
                "CI mode: fail on critical only")
                    confirm_and_run "uv run python manage.py check_health --fail-on-critical" ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/health.sh && shellcheck bin/cli/health.sh`
Expected: no output.

**Step 3: Drive it non-interactively (Back returns)**

Run:
```bash
printf '9\n' | bash -c 'source bin/lib/tuin.sh; source bin/lib/colors.sh; SCRIPT_DIR=bin; show_banner(){ :; }; run_command(){ :; }; confirm_and_run(){ :; }; source bin/cli/health.sh; health_menu; echo "RET=$?"'
```
Expected: the menu renders and the Back entry (index after the 8 options) returns `RET=0`. Confirm the Back index from the printed fallback list.

**Step 4: Commit**

```bash
git add bin/cli/health.sh
git commit -m "feat(cli): convert health submenu to tuin"
```

---

### Task 2.2: Convert pipeline.sh (free-text only)

**Files:**
- Modify: `bin/cli/pipeline.sh` (full rewrite)

**Step 1: Rewrite the file**

```bash
# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    while true; do
        show_banner
        if tuin_menu "Pipeline" \
            "Run pipeline (sample payload)" \
            "Run pipeline by definition" \
            "Run pipeline from file" \
            "Run checks only (orchestrated)" \
            "Run checks only (dry run)" \
            "List pipeline definitions" \
            "Show one pipeline definition" \
            "List recent pipeline runs" \
            "Show one pipeline run"
        then
            case $TUIN_REPLY in
                "Run pipeline (sample payload)")
                    confirm_and_run "uv run python manage.py run_pipeline --sample" ;;
                "Run pipeline by definition")
                    run_command "uv run python manage.py show_pipeline --all" "Available pipeline definitions"
                    pipeline_name=$(tuin_input "Enter pipeline definition name")
                    if [ -n "$pipeline_name" ]; then
                        confirm_and_run "uv run python manage.py run_pipeline --definition $pipeline_name"
                    else
                        echo -e "${RED}Pipeline definition name required${NC}"
                    fi ;;
                "Run pipeline from file")
                    payload_path=$(tuin_input "Enter path to payload file")
                    if [ -n "$payload_path" ]; then
                        confirm_and_run "uv run python manage.py run_pipeline --file $payload_path"
                    else
                        echo -e "${RED}File path required${NC}"
                    fi ;;
                "Run checks only (orchestrated)")
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only" ;;
                "Run checks only (dry run)")
                    confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run" ;;
                "List pipeline definitions")
                    confirm_and_run "uv run python manage.py show_pipeline --all" ;;
                "Show one pipeline definition")
                    pipeline_name=$(tuin_input "Enter pipeline definition name")
                    if [ -n "$pipeline_name" ]; then
                        confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                    else
                        echo -e "${RED}Pipeline definition name required${NC}"
                    fi ;;
                "List recent pipeline runs")
                    confirm_and_run "uv run python manage.py monitor_pipeline" ;;
                "Show one pipeline run")
                    run_id=$(tuin_input "Enter pipeline run id")
                    if [ -n "$run_id" ]; then
                        confirm_and_run "uv run python manage.py monitor_pipeline --run-id $run_id"
                    else
                        echo -e "${RED}Run id required${NC}"
                    fi ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/pipeline.sh && shellcheck bin/cli/pipeline.sh`
Expected: no output.

**Step 3: Commit**

```bash
git add bin/cli/pipeline.sh
git commit -m "feat(cli): convert pipeline submenu to tuin"
```

---

### Task 2.3: Convert intelligence.sh

**Files:**
- Modify: `bin/cli/intelligence.sh` (full rewrite — `intelligence_menu` + `custom_recommendations`)

**Step 1: Rewrite the file**

```bash
# Sourced by cli.sh — do not execute directly.

intelligence_menu() {
    local default_path="$PROJECT_DIR"
    while true; do
        show_banner
        tuin_section "Intelligence & Recommendations"
        echo "AI-powered recommendations for system optimization."
        echo ""
        if tuin_menu "Intelligence" \
            "Memory analysis" \
            "Disk analysis" \
            "Full analysis (memory + disk)" \
            "Custom options" \
            "List providers"
        then
            case $TUIN_REPLY in
                "Memory analysis")
                    confirm_and_run "uv run python manage.py get_recommendations --memory" ;;
                "Disk analysis")
                    disk_path=$(tuin_input "Enter path to analyze" "$default_path")
                    confirm_and_run "uv run python manage.py get_recommendations --disk --path=$disk_path" ;;
                "Full analysis (memory + disk)")
                    disk_path=$(tuin_input "Enter path for disk analysis" "$default_path")
                    confirm_and_run "uv run python manage.py get_recommendations --all --path=$disk_path" ;;
                "Custom options")
                    custom_recommendations ;;
                "List providers")
                    confirm_and_run "uv run python manage.py get_recommendations --list-providers" ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

custom_recommendations() {
    tuin_section "Configure custom analysis"

    local cmd="uv run python manage.py get_recommendations"

    if tuin_confirm "Include memory analysis?" y; then
        cmd="$cmd --memory"
    fi

    if tuin_confirm "Include disk analysis?" y; then
        cmd="$cmd --disk"
        disk_path=$(tuin_input "Path to analyze" "$PROJECT_DIR")
        cmd="$cmd --path=$disk_path"
    fi

    top_n=$(tuin_input "Top N processes" "10")
    if [ -n "$top_n" ]; then
        cmd="$cmd --top-n=$top_n"
    fi

    threshold_mb=$(tuin_input "Large file threshold MB" "100")
    if [ -n "$threshold_mb" ]; then
        cmd="$cmd --threshold-mb=$threshold_mb"
    fi

    if tuin_confirm "Output as JSON?" n; then
        cmd="$cmd --json"
    fi

    confirm_and_run "$cmd"
}
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/intelligence.sh && shellcheck bin/cli/intelligence.sh`
Expected: no output.

**Step 3: Commit**

```bash
git add bin/cli/intelligence.sh
git commit -m "feat(cli): convert intelligence submenu + custom wizard to tuin"
```

---

### Task 2.4: Convert notifications.sh

**Files:**
- Modify: `bin/cli/notifications.sh` (full rewrite — `notify_menu`, `test_notify_menu`, `test_notify_non_interactive`)

**Step 1: Rewrite the file (driver picker comes in Phase 3 — free-text for now)**

```bash
# Sourced by cli.sh — do not execute directly.

notify_menu() {
    while true; do
        show_banner
        if tuin_menu "Notifications" \
            "test_notify - Send a test notification"
        then
            case $TUIN_REPLY in
                "test_notify - Send a test notification")
                    test_notify_menu ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

test_notify_menu() {
    while true; do
        show_banner
        tuin_section "test_notify"
        echo "Send a test notification to verify driver configuration."
        echo ""
        if tuin_menu "test_notify" \
            "Interactive wizard (recommended)" \
            "Non-interactive (specify driver and flags)"
        then
            case $TUIN_REPLY in
                "Interactive wizard (recommended)")
                    confirm_and_run "uv run python manage.py test_notify" ;;
                "Non-interactive (specify driver and flags)")
                    test_notify_non_interactive ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}

test_notify_non_interactive() {
    local driver_name
    driver_name=$(tuin_input "Enter driver name (email/slack/pagerduty/generic)")
    if [ -z "$driver_name" ]; then
        echo -e "${RED}Driver name required${NC}"
        return
    fi

    local channel message
    channel=$(tuin_input "Enter channel (optional)")
    message=$(tuin_input "Enter custom message (optional)")

    local cmd="uv run python manage.py test_notify $driver_name --non-interactive"
    if [ -n "$channel" ]; then
        cmd="$cmd --channel=$channel"
    fi
    if [ -n "$message" ]; then
        cmd="$cmd --message=\"$message\""
    fi

    confirm_and_run "$cmd"
}
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/notifications.sh && shellcheck bin/cli/notifications.sh`
Expected: no output.

**Step 3: Commit**

```bash
git add bin/cli/notifications.sh
git commit -m "feat(cli): convert notifications submenus to tuin"
```

---

### Task 2.5: Convert cluster.sh

**Files:**
- Modify: `bin/cli/cluster.sh` (full rewrite)

**Step 1: Rewrite the file**

```bash
# Sourced by cli.sh — do not execute directly.

cluster_menu() {
    while true; do
        show_banner
        tuin_section "Cluster"
        echo "Push local check results to a hub instance (cluster mode)."
        echo ""
        if tuin_menu "Cluster" \
            "Push checks to hub" \
            "Push checks to hub (dry run)" \
            "Push checks to hub (specific checkers)"
        then
            case $TUIN_REPLY in
                "Push checks to hub")
                    confirm_and_run "uv run python manage.py push_to_hub" ;;
                "Push checks to hub (dry run)")
                    confirm_and_run "uv run python manage.py push_to_hub --dry-run" ;;
                "Push checks to hub (specific checkers)")
                    checker_names=$(tuin_input "Enter checker names (comma-separated)")
                    if [ -n "$checker_names" ]; then
                        confirm_and_run "uv run python manage.py push_to_hub --checkers $checker_names"
                    else
                        echo -e "${RED}Checker names required${NC}"
                    fi ;;
            esac
            echo ""
            tuin_input "Press Enter to continue" >/dev/null || true
        else
            return 0
        fi
    done
}
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/cluster.sh && shellcheck bin/cli/cluster.sh`
Expected: no output.

**Step 3: Run the full bats suite (all submenus now converted)**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
Expected: all pass.

**Step 4: Commit**

```bash
git add bin/cli/cluster.sh
git commit -m "feat(cli): convert cluster submenu to tuin"
```

---

## Phase 3 — Picker subsystem

### Task 3.1: Write failing tests for the parse helpers

**Files:**
- Create: `bin/tests/lib/test_pickers.bats`

**Step 1: Write the test file**

```bash
#!/usr/bin/env bats

setup() {
    load '../test_helper/common-setup'
    _common_setup
    source "$LIB_DIR/pickers.sh"
}

@test "parse_checker_names extracts names after the marker, skipping noise" {
    raw=$'System check identified some issues:\nWARNINGS:\n?: (checkers.W015) noise\nAvailable checkers:\n  cpu                  Check CPU usage.\n  memory               Check memory.\n  disk                 Check disk.'
    run parse_checker_names "$raw"
    assert_success
    assert_line --index 0 "cpu"
    assert_line --index 1 "memory"
    assert_line --index 2 "disk"
    [ "${#lines[@]}" -eq 3 ]
}

@test "parse_checker_names returns nothing when marker absent" {
    run parse_checker_names "no checkers here"
    assert_success
    [ "${#lines[@]}" -eq 0 ]
}

@test "parse_pipeline_names extracts quoted names incl. inactive" {
    raw=$'WARNINGS: noise\n--- Pipeline: "cli-test" ---\n  Flow: ctx\n--- Pipeline: "local-smart-2" ---\n  (inactive)\n  Flow: x'
    run parse_pipeline_names "$raw"
    assert_success
    assert_line --index 0 "cli-test"
    assert_line --index 1 "local-smart-2"
    [ "${#lines[@]}" -eq 2 ]
}

@test "pick_or_cancel non-TTY: selecting Cancel (index 1) returns non-zero" {
    run bash -c 'source bin/lib/tuin.sh; source bin/lib/pickers.sh; printf "1\n" | pick_or_cancel "Pick" alpha beta'
    assert_failure
}

@test "pick_or_cancel non-TTY: selecting first real option returns its value" {
    run bash -c 'source bin/lib/tuin.sh; source bin/lib/pickers.sh; printf "2\n" | pick_or_cancel "Pick" alpha beta'
    assert_success
    assert_output --partial "alpha"
}
```

**Step 2: Run to verify they fail**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_pickers.bats`
Expected: FAIL (functions not defined / pickers.sh is still the placeholder).

---

### Task 3.2: Implement pickers.sh

**Files:**
- Modify: `bin/lib/pickers.sh` (replace the Phase 1 placeholder)

**Step 1: Write the helpers**

```bash
# Sourced by cli.sh — picker helpers for tuin-based menus.
# Do not execute directly.
[[ -n "${_LIB_PICKERS_LOADED:-}" ]] && return 0
_LIB_PICKERS_LOADED=1

# parse_checker_names <raw-output-of:check_health --list>
# Emits one checker name per line. Anchors on the "Available checkers:" marker
# (skipping Django system-check noise) and stops at the first blank line.
parse_checker_names() {
    awk '
        /^Available checkers:/ { grab=1; next }
        grab && /^[[:space:]]*$/ { grab=0 }
        grab && /^[[:space:]]+[^[:space:]]/ { print $1 }
    ' <<<"$1"
}

# parse_pipeline_names <raw-output-of:show_pipeline --all>
# Emits one definition name per line (including inactive ones), in order.
parse_pipeline_names() {
    sed -nE 's/^--- Pipeline: "([^"]+)" ---.*/\1/p' <<<"$1"
}

# pick_or_cancel <title> <option...>
# Shows a tuin_choose picker with a prepended "← Cancel" entry.
# Prints the chosen value to stdout, or returns non-zero on Cancel/Ctrl-C/empty.
pick_or_cancel() {
    local title="$1"; shift
    [ "$#" -eq 0 ] && return 1
    [ -n "$title" ] && tuin_section "$title" >&2
    local choice
    choice="$(tuin_choose "← Cancel" "$@")" || return 1
    [ "$choice" = "← Cancel" ] && return 1
    printf '%s\n' "$choice"
}
```

> **Bash 3.2 note:** `<<<` here-strings and `awk`/`sed` are all 3.2-safe.

**Step 2: Run the picker tests**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/test_pickers.bats`
Expected: all pass.

**Step 3: shellcheck**

Run: `shellcheck bin/lib/pickers.sh`
Expected: no output.

**Step 4: Commit**

```bash
git add bin/lib/pickers.sh bin/tests/lib/test_pickers.bats
git commit -m "feat(cli): add picker subsystem (parse helpers + pick_or_cancel)"
```

---

### Task 3.3: Wire the checker picker into health.sh

**Files:**
- Modify: `bin/cli/health.sh` ("Run a single checker" arm)

**Step 1: Replace the "Run a single checker" arm**

```bash
                "Run a single checker")
                    raw=$(tuin_spin "Loading checkers" -- uv run python manage.py check_health --list 2>/dev/null) || true
                    names=()
                    while IFS= read -r _line; do
                        [ -n "$_line" ] && names+=("$_line")
                    done < <(parse_checker_names "$raw")
                    if [ "${#names[@]}" -eq 0 ]; then
                        echo -e "${RED}No checkers available (command failed or empty)${NC}"
                    elif name=$(pick_or_cancel "Select a checker" "${names[@]}"); then
                        confirm_and_run "uv run python manage.py run_check $name"
                    fi ;;
```

> **Verify during this task:** confirm `tuin_spin` writes its spinner to `/dev/tty` (not stderr) so the `2>/dev/null` on the inner command drops only Django warnings, not the spinner. If the spinner disappears, drop the `2>/dev/null` and rely on `parse_checker_names` anchoring to skip the noise.

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/health.sh && shellcheck bin/cli/health.sh`
Expected: no output.

**Step 3: Interactive smoke**

Run: `bash bin/cli.sh health` — choose "Run a single checker", confirm the arrow-picker lists real checkers, `← Cancel` returns, and a pick reaches the confirm prompt.

**Step 4: Commit**

```bash
git add bin/cli/health.sh
git commit -m "feat(cli): arrow-pick a single checker in health menu"
```

---

### Task 3.4: Wire the pipeline-definition picker into pipeline.sh

**Files:**
- Modify: `bin/cli/pipeline.sh` ("Run pipeline by definition" and "Show one pipeline definition" arms)

**Step 1: Replace the "Run pipeline by definition" arm**

```bash
                "Run pipeline by definition")
                    raw=$(tuin_spin "Loading definitions" -- uv run python manage.py show_pipeline --all 2>/dev/null) || true
                    defs=()
                    while IFS= read -r _line; do
                        [ -n "$_line" ] && defs+=("$_line")
                    done < <(parse_pipeline_names "$raw")
                    if [ "${#defs[@]}" -eq 0 ]; then
                        echo -e "${RED}No pipeline definitions available${NC}"
                    elif pipeline_name=$(pick_or_cancel "Select a definition" "${defs[@]}"); then
                        confirm_and_run "uv run python manage.py run_pipeline --definition $pipeline_name"
                    fi ;;
```

**Step 2: Replace the "Show one pipeline definition" arm**

```bash
                "Show one pipeline definition")
                    raw=$(tuin_spin "Loading definitions" -- uv run python manage.py show_pipeline --all 2>/dev/null) || true
                    defs=()
                    while IFS= read -r _line; do
                        [ -n "$_line" ] && defs+=("$_line")
                    done < <(parse_pipeline_names "$raw")
                    if [ "${#defs[@]}" -eq 0 ]; then
                        echo -e "${RED}No pipeline definitions available${NC}"
                    elif pipeline_name=$(pick_or_cancel "Select a definition" "${defs[@]}"); then
                        confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                    fi ;;
```

**Step 3: Verify syntax + shellcheck**

Run: `bash -n bin/cli/pipeline.sh && shellcheck bin/cli/pipeline.sh`
Expected: no output.

**Step 4: Interactive smoke**

Run: `bash bin/cli.sh pipeline` — both flows show an arrow-picker of real definitions.

**Step 5: Commit**

```bash
git add bin/cli/pipeline.sh
git commit -m "feat(cli): arrow-pick pipeline definitions in pipeline menu"
```

---

### Task 3.5: Wire the static driver picker into notifications.sh

**Files:**
- Modify: `bin/cli/notifications.sh` (`test_notify_non_interactive`)

**Step 1: Replace the driver-name prompt**

Replace the `driver_name=$(tuin_input …)` block with:
```bash
    local driver_name
    if ! driver_name=$(pick_or_cancel "Driver" email slack pagerduty generic); then
        return
    fi
```

**Step 2: Verify syntax + shellcheck**

Run: `bash -n bin/cli/notifications.sh && shellcheck bin/cli/notifications.sh`
Expected: no output.

**Step 3: Commit**

```bash
git add bin/cli/notifications.sh
git commit -m "feat(cli): arrow-pick notify driver in test_notify flow"
```

---

## Phase 4 — Docs + final pass

### Task 4.1: Update docs

**Files:**
- Modify: `bin/README.md` (menu UI description, if present)
- Modify: `bin/AGENTS.md` ("Key modules" — note `lib/tuin.sh`, `lib/tuin_vendor.sh`, `lib/pickers.sh`)

**Step 1: Update bin/AGENTS.md Key modules list**

Add bullets noting tuin is the CLI UI library (vendored, pinned via `tuin_vendor.sh`) and `lib/pickers.sh` provides arrow-pickers that parse `manage.py` list output.

**Step 2: Update bin/README.md** if it documents the old `select` menus.

**Step 3: Commit**

```bash
git add bin/README.md bin/AGENTS.md
git commit -m "docs(cli): document tuin UI + picker subsystem in bin/"
```

---

### Task 4.2: Final verification pass

**Step 1: Full bats suite**

Run: `./bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/ bin/tests/`
Expected: all pass.

**Step 2: shellcheck all touched shell (never the vendored tuin.sh)**

Run: `shellcheck bin/cli.sh bin/cli/*.sh bin/lib/tuin_vendor.sh bin/lib/pickers.sh`
Expected: no output.

**Step 3: Interactive smoke test**

Run: `bash bin/cli.sh`
Verify: arrow-key main menu; each submenu loops; Back returns up a level; Exit quits; confirmations, free-text inputs, and all three pickers (checker, definition, driver) work; `← Cancel` returns cleanly.

**Step 4: Non-TTY regression**

Run: `printf '1\n' | bash bin/cli.sh health 2>&1 | tail -20`
Verify: the numbered fallback renders and the action runs (no hang).

**Step 5: Open the PR**

```bash
git push -u origin design/tuin-cli-ui
gh pr create --title "feat(cli): tuin as the bin/cli.sh UI" --body "Implements docs/plans/2026-06-02-tuin-cli-ui-design.md — vendored tuin v0.3.0, converted menus, and arrow-pickers for single-checker / pipeline-definition / notify-driver flows."
```

---

## Acceptance criteria (from design)

- [ ] Vendored tuin committed & sourced; `ensure_tuin` in install.
- [ ] Main menu + all five submenus converted to tuin.
- [ ] Pickers working (checker, definition, driver) with `← Cancel` and empty-list handling.
- [ ] Untouched files unchanged (`install.sh` only gains `ensure_tuin`).
- [ ] bats green (incl. `test_pickers.bats`); shellcheck clean (excluding vendored `tuin.sh`).
- [ ] Interactive + non-TTY smoke pass.
- [ ] Future enhancement noted: machine-readable `manage.py` flags (`docs/plans/2026-06-02-tuin-cli-ui-design.md`).