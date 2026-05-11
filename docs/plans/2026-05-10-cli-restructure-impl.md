---
title: "Interactive CLI Restructure Implementation Plan"
parent: Plans
---

# Interactive CLI Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure `bin/cli.sh` from 8 menus + Exit (with 4 broken invocations and 4 misleading sections) to 7 menus + Exit, fix every broken invocation, expose 2 unused commands (`push_to_hub`, `setup_instance`), and verify with BATS smoke tests.

**Architecture:** Pure shell-script restructure. Delete `bin/cli/alerts.sh` and `bin/cli/system.sh`. Rewrite `bin/cli/health.sh` (absorbs `run_check`, `preflight`) and `bin/cli/pipeline.sh` (fixes 4 broken invocations, absorbs `--checks-only`, flattens submenus). Create `bin/cli/cluster.sh` for `push_to_hub`. Extend `bin/cli/install_menu.sh` with `setup_instance` and `set_production.sh`. Update `bin/cli.sh` (sourcing, main menu, jump commands, help). Add 6 BATS tests for the new menu set and the deleted-jump-command behavior.

**Tech Stack:** Bash 5.x, BATS (Bash Automated Testing System), Django management commands as the backing operations.

**Design doc:** `docs/plans/2026-05-10-cli-restructure-design.md`

**Branch:** `refactor/cli-restructure` (already created from `main`, design doc committed at `2714391`).

**Single PR with one logical commit.** The restructure has internal consistency — the menu can't render correctly mid-way through the change.

---

## Background — what's changing

```
bin/cli/
├── install_menu.sh    EXTEND   (+2 items: setup_instance, set_production.sh)
├── health.sh          REWRITE  (8 items + Back; absorbs run_check from alerts, preflight from system)
├── alerts.sh          DELETE   (contents redistributed)
├── intelligence.sh    UNCHANGED
├── pipeline.sh        REWRITE  (flat 9 items + Back; fixes 4 broken invocations)
├── notifications.sh   UNCHANGED
├── system.sh          DELETE   (contents redistributed)
├── update.sh          UNCHANGED
└── cluster.sh         CREATE   (3 push_to_hub items + Back)
```

Top-level menu in `bin/cli.sh` goes from:

```
1. Install / Setup Project
2. Health & Monitoring
3. Alerts & Incidents          ← misleading, deleted
4. Intelligence & Recommendations
5. Pipeline Orchestration
6. Notifications
7. Updates
8. System & Security            ← misleading, deleted
9. Exit
```

to:

```
1. Install / Setup
2. Health
3. Pipeline
4. Intelligence
5. Notifications
6. Cluster                       ← NEW (push_to_hub)
7. Updates
8. Exit
```

**Broken invocations being fixed:**

| Old (broken) | New (fixed) |
|---|---|
| `run_check --list` (in alerts.sh) | dropped — `check_health --list` already exists in Health |
| `run_pipeline --list` | `show_pipeline --all` |
| `run_pipeline <name>` | `run_pipeline --definition <name>` |
| `run_pipeline <name> --dry-run` | `run_pipeline --definition <name> --dry-run` (or `--checks-only --dry-run` for the checks-only variant) |
| `monitor_pipeline --list` | `monitor_pipeline` (default lists) |
| `monitor_pipeline <id>` | `monitor_pipeline --run-id <id>` |
| `monitor_pipeline <id> --follow` | dropped — no such flag |

---

## Task 1: Create the new Cluster menu module

**Files to create:**
- `bin/cli/cluster.sh`

**Step 1: Write the file**

```bash
# Sourced by cli.sh — do not execute directly.

cluster_menu() {
    show_banner
    echo -e "${BOLD}═══ Cluster ═══${NC}"
    echo ""
    echo "Push local check results to a hub instance (cluster mode)"
    echo ""
    echo -e "${BOLD}Available options:${NC}"
    echo "  --dry-run          Show what would be pushed without sending"
    echo "  --checkers a,b,c   Run only specific checkers (comma-separated)"
    echo ""

    local options=(
        "Push checks to hub"
        "Push checks to hub (dry run)"
        "Push checks to hub (specific checkers)"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py push_to_hub"
                ;;
            2)
                confirm_and_run "uv run python manage.py push_to_hub --dry-run"
                ;;
            3)
                read -p "Enter checker names (comma-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py push_to_hub --checkers $checker_names"
                else
                    echo -e "${RED}Checker names required${NC}"
                fi
                ;;
            4)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Verify syntax**

```bash
bash -n bin/cli/cluster.sh
```
Expected: exit 0, no output.

---

## Task 2: Rewrite the Health menu

The current `bin/cli/health.sh` has 6 items. The new version has 8 items + Back, absorbing `run_check` (from alerts.sh) and `preflight` (from system.sh).

**Files to modify:**
- `bin/cli/health.sh`

**Step 1: Replace the entire file**

```bash
# Sourced by cli.sh — do not execute directly.

health_menu() {
    show_banner
    echo -e "${BOLD}═══ Health ═══${NC}"
    echo ""

    local options=(
        "Run all health checks"
        "Run specific checkers"
        "Run a single checker"
        "List available checkers"
        "Preflight dashboard"
        "JSON output (all checks)"
        "CI mode: fail on warning"
        "CI mode: fail on critical only"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py check_health"
                ;;
            2)
                echo ""
                run_command "uv run python manage.py check_health --list" "Listing checkers"
                echo ""
                read -p "Enter checker names (space-separated): " checker_names
                if [ -n "$checker_names" ]; then
                    confirm_and_run "uv run python manage.py check_health $checker_names"
                else
                    echo -e "${RED}No checkers specified${NC}"
                fi
                ;;
            3)
                echo ""
                run_command "uv run python manage.py check_health --list" "Available checkers"
                echo ""
                read -p "Enter checker name: " checker_name
                if [ -n "$checker_name" ]; then
                    confirm_and_run "uv run python manage.py run_check $checker_name"
                else
                    echo -e "${RED}Checker name required${NC}"
                fi
                ;;
            4)
                run_command "uv run python manage.py check_health --list" "Available checkers"
                ;;
            5)
                confirm_and_run "uv run python manage.py preflight"
                ;;
            6)
                confirm_and_run "uv run python manage.py check_health --json"
                ;;
            7)
                confirm_and_run "uv run python manage.py check_health --fail-on-warning"
                ;;
            8)
                confirm_and_run "uv run python manage.py check_health --fail-on-critical"
                ;;
            9)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Verify syntax**

```bash
bash -n bin/cli/health.sh
```
Expected: exit 0.

---

## Task 3: Rewrite the Pipeline menu

The current `bin/cli/pipeline.sh` is a 4-item top with 3 submenus (15 items total) and 4 broken invocations. The new version is a flat 9-item menu + Back.

**Files to modify:**
- `bin/cli/pipeline.sh`

**Step 1: Replace the entire file**

```bash
# Sourced by cli.sh — do not execute directly.

pipeline_menu() {
    show_banner
    echo -e "${BOLD}═══ Pipeline ═══${NC}"
    echo ""

    local options=(
        "Run pipeline (sample payload)"
        "Run pipeline by definition"
        "Run pipeline from file"
        "Run checks only (orchestrated)"
        "Run checks only (dry run)"
        "List pipeline definitions"
        "Show one pipeline definition"
        "List recent pipeline runs"
        "Show one pipeline run"
        "Back to main menu"
    )

    # shellcheck disable=SC2034
    select opt in "${options[@]}"; do
        case $REPLY in
            1)
                confirm_and_run "uv run python manage.py run_pipeline --sample"
                ;;
            2)
                echo ""
                run_command "uv run python manage.py show_pipeline --all" "Available pipeline definitions"
                echo ""
                read -p "Enter pipeline definition name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --definition $pipeline_name"
                else
                    echo -e "${RED}Pipeline definition name required${NC}"
                fi
                ;;
            3)
                read -p "Enter path to payload file: " payload_path
                if [ -n "$payload_path" ]; then
                    confirm_and_run "uv run python manage.py run_pipeline --file $payload_path"
                else
                    echo -e "${RED}File path required${NC}"
                fi
                ;;
            4)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only"
                ;;
            5)
                confirm_and_run "uv run python manage.py run_pipeline --checks-only --dry-run"
                ;;
            6)
                confirm_and_run "uv run python manage.py show_pipeline --all"
                ;;
            7)
                read -p "Enter pipeline definition name: " pipeline_name
                if [ -n "$pipeline_name" ]; then
                    confirm_and_run "uv run python manage.py show_pipeline --name $pipeline_name"
                else
                    echo -e "${RED}Pipeline definition name required${NC}"
                fi
                ;;
            8)
                confirm_and_run "uv run python manage.py monitor_pipeline"
                ;;
            9)
                read -p "Enter pipeline run id: " run_id
                if [ -n "$run_id" ]; then
                    confirm_and_run "uv run python manage.py monitor_pipeline --run-id $run_id"
                else
                    echo -e "${RED}Run id required${NC}"
                fi
                ;;
            10)
                return
                ;;
            *)
                echo -e "${RED}Invalid option${NC}"
                ;;
        esac
        break
    done
}
```

**Step 2: Verify syntax**

```bash
bash -n bin/cli/pipeline.sh
```
Expected: exit 0.

---

## Task 4: Extend the Install menu with `setup_instance` and `set_production.sh`

**Files to modify:**
- `bin/cli/install_menu.sh`

**Step 1: Add 2 new items to the `options` array**

Find the `options=(...)` array (lines 8-20) and replace with:

```bash
    local options=(
        "Full installation (all steps)"
        "Environment & .env configuration"
        "Celery / Redis broker setup"
        "Cluster (multi-instance) setup"
        "Install dependencies (uv sync)"
        "Run migrations & system checks"
        "Set up cron jobs"
        "Set up shell aliases"
        "Deploy (Docker / systemd)"
        "Check installation status"
        "Set up monitoring instance (interactive wizard)"
        "Set production mode"
        "Back to main menu"
    )
```

**Step 2: Add 2 new case branches before the `Back` case**

Find the `case $REPLY in ... 11) return ;;` block (lines 24-37) and replace with:

```bash
    select opt in "${options[@]}"; do
        case $REPLY in
            1)  run_command "$SCRIPT_DIR/install.sh" "Full installation" ;;
            2)  run_command "$SCRIPT_DIR/install.sh env" "Environment setup" ;;
            3)  run_command "$SCRIPT_DIR/install.sh celery" "Celery setup" ;;
            4)  run_command "$SCRIPT_DIR/install.sh cluster" "Cluster setup" ;;
            5)  run_command "$SCRIPT_DIR/install.sh deps" "Installing dependencies" ;;
            6)  run_command "$SCRIPT_DIR/install.sh migrate" "Migrations & checks" ;;
            7)  run_command "$SCRIPT_DIR/install.sh cron" "Cron setup" ;;
            8)  run_command "$SCRIPT_DIR/install.sh aliases" "Shell aliases" ;;
            9)  run_command "$SCRIPT_DIR/install.sh deploy" "Deployment" ;;
            10) check_installation ;;
            11) confirm_and_run "uv run python manage.py setup_instance" ;;
            12) confirm_and_run "$SCRIPT_DIR/set_production.sh" ;;
            13) return ;;
            *)  echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
```

**Step 3: Verify syntax**

```bash
bash -n bin/cli/install_menu.sh
```
Expected: exit 0.

---

## Task 5: Delete the Alerts and System menu modules

**Files to delete:**
- `bin/cli/alerts.sh`
- `bin/cli/system.sh`

**Step 1: Remove with git**

```bash
git rm bin/cli/alerts.sh bin/cli/system.sh
```

**Step 2: Verify they're gone**

```bash
ls bin/cli/
```
Expected: shows `cluster.sh`, `health.sh`, `install_menu.sh`, `intelligence.sh`, `notifications.sh`, `pipeline.sh`, `update.sh`. No `alerts.sh`, no `system.sh`.

---

## Task 6: Update `bin/cli.sh` (sourcing, main menu, jump commands, help)

**Files to modify:**
- `bin/cli.sh`

This task has many small edits. Do them in order to avoid line-number drift.

**Step 1: Update the comment header (lines 6-16)**

Find:
```bash
# Commands:
#   (no args)    Start interactive mode
#   help         Show help message
#   install      Jump to installation menu
#   health       Jump to health monitoring
#   alerts       Jump to alerts menu
#   intel        Jump to intelligence menu
#   pipeline     Jump to pipeline menu
#   notify       Jump to notifications menu
#   update       Jump to updates menu
#   system       Jump to system & security menu
#
```

Replace with:
```bash
# Commands:
#   (no args)    Start interactive mode
#   help         Show help message
#   install      Jump to installation menu
#   health       Jump to health menu
#   pipeline     Jump to pipeline menu
#   intel        Jump to intelligence menu
#   notify       Jump to notifications menu
#   cluster      Jump to cluster menu
#   update       Jump to updates menu
#
```

(Removed `alerts` and `system`; added `cluster`; reordered to match main-menu order.)

**Step 2: Update `show_help()` (lines 47-64)**

Find:
```bash
show_help() {
    echo "Usage: ./bin/cli.sh [command]"
    echo ""
    echo "Interactive CLI for Server Maintenance"
    echo ""
    echo "Commands:"
    echo "  (no args)    Start interactive mode"
    echo "  help         Show this help message"
    echo "  install      Jump to installation menu"
    echo "  health       Jump to health monitoring"
    echo "  alerts       Jump to alerts menu"
    echo "  intel        Jump to intelligence menu"
    echo "  pipeline     Jump to pipeline menu"
    echo "  notify       Jump to notifications menu"
    echo "  update       Jump to updates menu"
    echo "  system       Jump to system & security menu"
    echo ""
}
```

Replace with:
```bash
show_help() {
    echo "Usage: ./bin/cli.sh [command]"
    echo ""
    echo "Interactive CLI for Server Maintenance"
    echo ""
    echo "Commands:"
    echo "  (no args)    Start interactive mode"
    echo "  help         Show this help message"
    echo "  install      Jump to installation menu"
    echo "  health       Jump to health menu"
    echo "  pipeline     Jump to pipeline menu"
    echo "  intel        Jump to intelligence menu"
    echo "  notify       Jump to notifications menu"
    echo "  cluster      Jump to cluster menu"
    echo "  update       Jump to updates menu"
    echo ""
}
```

**Step 3: Update the source block (lines 105-112)**

Find:
```bash
source "$SCRIPT_DIR/cli/install_menu.sh"
source "$SCRIPT_DIR/cli/health.sh"
source "$SCRIPT_DIR/cli/alerts.sh"
source "$SCRIPT_DIR/cli/intelligence.sh"
source "$SCRIPT_DIR/cli/pipeline.sh"
source "$SCRIPT_DIR/cli/notifications.sh"
source "$SCRIPT_DIR/cli/update.sh"
source "$SCRIPT_DIR/cli/system.sh"
```

Replace with:
```bash
source "$SCRIPT_DIR/cli/install_menu.sh"
source "$SCRIPT_DIR/cli/health.sh"
source "$SCRIPT_DIR/cli/pipeline.sh"
source "$SCRIPT_DIR/cli/intelligence.sh"
source "$SCRIPT_DIR/cli/notifications.sh"
source "$SCRIPT_DIR/cli/cluster.sh"
source "$SCRIPT_DIR/cli/update.sh"
```

**Step 4: Update `show_main_menu()` (lines 118-149)**

Find:
```bash
show_main_menu() {
    echo -e "${BOLD}Select an option:${NC}"
    echo ""

    local options=(
        "Install / Setup Project"
        "Health & Monitoring"
        "Alerts & Incidents"
        "Intelligence & Recommendations"
        "Pipeline Orchestration"
        "Notifications"
        "Updates"
        "System & Security"
        "Exit"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) install_project ;;
            2) health_menu ;;
            3) alerts_menu ;;
            4) intelligence_menu ;;
            5) pipeline_menu ;;
            6) notify_menu ;;
            7) update_menu ;;
            8) system_menu ;;
            9) echo -e "${GREEN}Goodbye!${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}
```

Replace with:
```bash
show_main_menu() {
    echo -e "${BOLD}Select an option:${NC}"
    echo ""

    local options=(
        "Install / Setup"
        "Health"
        "Pipeline"
        "Intelligence"
        "Notifications"
        "Cluster"
        "Updates"
        "Exit"
    )

    select opt in "${options[@]}"; do
        case $REPLY in
            1) install_project ;;
            2) health_menu ;;
            3) pipeline_menu ;;
            4) intelligence_menu ;;
            5) notify_menu ;;
            6) cluster_menu ;;
            7) update_menu ;;
            8) echo -e "${GREEN}Goodbye!${NC}"; exit 0 ;;
            *) echo -e "${RED}Invalid option${NC}" ;;
        esac
        break
    done
}
```

**Step 5: Update the `main()` dispatch (lines 155-222)**

Find the `case "${1:-}" in ... esac` block. Within it:

- **Remove** the `alerts)` and `system)` cases entirely (8 lines each, including the `;;`).
- **Add** a `cluster)` case mirroring the others.

The new dispatch block should be:

```bash
    case "${1:-}" in
        help|--help|-h)
            show_help
            exit 0
            ;;
        install)
            show_banner
            install_project
            echo ""
            read -p "Press Enter to continue..."
            ;;
        health)
            show_banner
            health_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        intel|intelligence)
            show_banner
            intelligence_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        pipeline)
            show_banner
            pipeline_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        notify)
            show_banner
            notify_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        cluster)
            show_banner
            cluster_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        update)
            show_banner
            update_menu
            echo ""
            read -p "Press Enter to continue..."
            ;;
        "")
            while true; do
                show_banner
                show_main_menu
                echo ""
                read -p "Press Enter to continue..."
            done
            ;;
        *)
            echo "Unknown command: $1"
            show_help
            exit 1
            ;;
    esac
```

(Note the case order matches the main-menu numerical order: install, health, pipeline, intel, notify, cluster, update.)

**Step 6: Verify syntax**

```bash
bash -n bin/cli.sh
```
Expected: exit 0.

---

## Task 7: Add BATS smoke tests for the new structure

**Files to modify:**
- `bin/tests/test_cli.bats`

**Step 1: Append the new tests to the file**

Add these tests after the existing 4 tests:

```bash
@test "cli.sh --help mentions cluster jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    assert_output --partial "cluster"
}

@test "cli.sh --help no longer mentions alerts jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    refute_output --partial "alerts"
}

@test "cli.sh --help no longer mentions system jump command" {
    run "$BIN_DIR/cli.sh" --help
    assert_success
    refute_output --partial "system"
}

@test "cli.sh alerts is now an unknown command" {
    run "$BIN_DIR/cli.sh" alerts
    assert_failure
    assert_output --partial "Unknown command"
}

@test "cli.sh system is now an unknown command" {
    run "$BIN_DIR/cli.sh" system
    assert_failure
    assert_output --partial "Unknown command"
}

@test "bin/cli/alerts.sh and bin/cli/system.sh do not exist" {
    [ ! -f "$BIN_DIR/cli/alerts.sh" ]
    [ ! -f "$BIN_DIR/cli/system.sh" ]
}

@test "bin/cli/cluster.sh exists" {
    [ -f "$BIN_DIR/cli/cluster.sh" ]
}
```

**Step 2: Run the BATS tests**

```bash
bin/tests/bats-core/bin/bats bin/tests/test_cli.bats
```

Or if the project has a BATS runner script:

```bash
bash bin/tests/run_tests.sh test_cli.bats
```

(Check `bin/tests/` for the canonical runner. If neither exists, install `bats` locally and run `bats bin/tests/test_cli.bats`.)

Expected: all tests pass (4 existing + 7 new = 11 total).

If "all cli modules pass syntax check" fails, one of the rewritten modules has a syntax error — bash -n it and fix.

---

## Task 8: Verify, lint, manual sanity check

**Step 1: Full BATS suite**

```bash
bin/tests/bats-core/bin/bats bin/tests/
```

Or, if there's no central runner, run each `*.bats` file individually:

```bash
for f in bin/tests/*.bats; do
    bin/tests/bats-core/bin/bats "$f" || exit 1
done
```

Expected: all tests across all `.bats` files pass. The `test_install.bats`, `test_set_production.bats`, `test_update.bats` should be unaffected (no changes to those scripts).

**Step 2: Python suite (no Python files touched, but pre-commit will run)**

```bash
uv run pytest apps/ 2>&1 | tail -3
```

Expected: 1941 passed (or whatever the current baseline is). No regressions from a CLI-only change.

**Step 3: Manual interactive sanity check**

Don't drive the full menu — just spot-check the high-risk paths:

```bash
# Help text
./bin/cli.sh --help | grep -E "alerts|system|cluster"
# Expected: only "cluster" appears; no "alerts" or "system" lines.

# Deleted jump commands fail correctly
./bin/cli.sh alerts; echo "exit: $?"
./bin/cli.sh system; echo "exit: $?"
# Expected: both print "Unknown command" and exit 1.

# New cluster jump opens the menu (just verify it doesn't crash on launch — Ctrl-C out)
./bin/cli.sh cluster
# Expected: shows the "═══ Cluster ═══" header and the 4 options. Pick "Back" or Ctrl-C.

# Pipeline list-definitions (was broken via run_pipeline --list)
echo "y" | ./bin/cli.sh pipeline
# Then in the prompt: pick option 6 ("List pipeline definitions")
# Expected: actually runs `show_pipeline --all` and either lists definitions or emits an empty-state message.
# (If you prefer to skip the interactive walk, you can verify by running directly:
#   uv run python manage.py show_pipeline --all
#   uv run python manage.py monitor_pipeline
#   uv run python manage.py setup_instance --help
#   uv run python manage.py push_to_hub --dry-run
# and confirming each command exists and runs without argparse errors.)
```

If anything looks wrong, STOP and report.

---

## Task 9: Commit

```bash
git add bin/cli.sh bin/cli/cluster.sh bin/cli/health.sh bin/cli/pipeline.sh bin/cli/install_menu.sh bin/tests/test_cli.bats
# (alerts.sh and system.sh deletions are already staged via git rm in Task 5)
git commit -m "$(cat <<'EOF'
refactor(cli): restructure menus, fix 4 broken invocations, expose 2 commands

The interactive CLI in bin/cli.sh and bin/cli/*.sh drifted out of
alignment with the underlying Django commands:

  Broken invocations (4):
    - bin/cli/alerts.sh   - run_check --list (no such flag)
    - bin/cli/pipeline.sh - run_pipeline --list (use show_pipeline --all)
    - bin/cli/pipeline.sh - run_pipeline <name> (use --definition <name>)
    - bin/cli/pipeline.sh - monitor_pipeline --list/<id>/--follow
                            (use defaults / --run-id; --follow doesn't exist)

  Misleading menus (4):
    - "Alerts & Incidents"  - contained run_check + run_pipeline,
                              not alerts or incidents
    - "System & Security"   - only had preflight + a shell script
    - preflight             - lived under "System" but is a checkers command
    - run_check             - lived under "Alerts" but is a checker

  Unexposed commands (2):
    - alerts:push_to_hub             - cluster-mode push, no menu entry
    - orchestration:setup_instance   - interactive wizard, no menu entry

Restructure: 8 menus + Exit -> 7 menus + Exit. Delete the misleading
"Alerts & Incidents" and "System & Security" menus and redistribute
their contents to Health, Pipeline, and Install. New Cluster menu
hosts push_to_hub. Pipeline menu collapses 3 submenus into a flat
9-item layout with all 4 broken invocations fixed.

cli.sh alerts and cli.sh system jump commands are removed (no
deprecation shim — they were misleading from day one). cli.sh cluster
is added.

7 new BATS smoke tests verify deleted jump commands now error,
help text reflects the new layout, and the new cluster module exists.

Design doc: docs/plans/2026-05-10-cli-restructure-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Pre-commit hooks should pass (no Python files touched; black/ruff/mypy/pytest all skip or pass trivially). After commit, `git status` clean. `git log --oneline main..HEAD` should show 2 commits (design doc + this fix).

---

## Task 10: Push and open PR

```bash
git push -u origin refactor/cli-restructure
gh pr create --base main --title "refactor(cli): restructure interactive menus, fix 4 broken invocations, expose 2 commands" --body "$(cat <<'EOF'
## Summary
- Audit of `bin/cli.sh` found 4 broken invocations, 4 misleading menu sections, and 2 management commands not exposed anywhere.
- Restructures from 8 menus + Exit to 7 menus + Exit. Deletes the misleading "Alerts & Incidents" and "System & Security" menus. Adds a new **Cluster** menu for `push_to_hub`. Exposes `setup_instance` (Django wizard) under Install.
- Pipeline menu collapses 3 submenus into a flat 9-item layout with all 4 broken invocations fixed (`run_pipeline --list` → `show_pipeline --all`; `run_pipeline <name>` → `--definition <name>`; `monitor_pipeline --list/<id>/--follow` → defaults / `--run-id`; `--follow` dropped — no such flag).
- `cli.sh alerts` and `cli.sh system` jump commands are removed (no deprecation shim; they were misleading from day one). `cli.sh cluster` added.
- 7 new BATS smoke tests verify the new structure.

Design doc: `docs/plans/2026-05-10-cli-restructure-design.md`

## Why
User report: "Alerts and incidents makes no sense" — they hit the broken `run_check --list` (which `bin/cli/alerts.sh:49` invokes but `run_check` rejects) and noted the menu's contents have nothing to do with alerts or incidents. The audit found similar structural issues in Pipeline and System menus.

## New top-level menu
| # | Menu | Source(s) |
|---|---|---|
| 1 | Install / Setup | existing + `setup_instance` (NEW) + `set_production.sh` (moved from System) |
| 2 | Health | `check_health` + `run_check` (moved from Alerts) + `preflight` (moved from System) |
| 3 | Pipeline | `run_pipeline` (fixed) + `show_pipeline` + `monitor_pipeline` (fixed) + `--checks-only` (moved from Alerts) |
| 4 | Intelligence | unchanged |
| 5 | Notifications | unchanged |
| 6 | **Cluster** (new) | `push_to_hub` (newly exposed) |
| 7 | Updates | unchanged |
| 8 | Exit | unchanged |

## Behavior changes for users
1. **Two menus disappear** ("Alerts & Incidents", "System & Security"); their contents are now under Health, Pipeline, Install.
2. **One menu appears** ("Cluster") for the previously-unreachable `push_to_hub` command.
3. **Two jump commands removed** (`cli.sh alerts`, `cli.sh system`) — both will print "Unknown command" and exit 1.
4. **One jump command added** (`cli.sh cluster`).
5. **Pipeline menu flattens** — 3 submenus collapse into one 9-item menu.

## Out of scope
- Surfacing every underexposed flag (`run_pipeline --notify-driver`, `--label`, `--trace-id`; `check_health --warning-threshold`; `test_notify --severity`; per-driver `test_notify` flags; `monitor_pipeline --status`/`--limit`; etc.). The audit listed many. Adding them all would balloon the menus; power users invoke `--help` directly. A future "Custom flags per command" feature is a separate design.
- Intelligence menu's surprising `--path=$PROJECT_DIR` default for disk recommendations (the underlying `get_recommendations` defaults to `/`). Noted but not changed.

## Test plan
- [x] `bash -n` passes on all rewritten and new menu modules
- [x] BATS smoke tests pass (4 existing + 7 new)
- [x] `uv run pytest apps/` — full suite green (no Python files touched)
- [x] Manual: `cli.sh --help` shows the new layout; `cli.sh alerts` and `cli.sh system` error out; `cli.sh cluster` opens the new menu
- [x] Manual: each fixed Pipeline invocation actually runs (e.g., "List pipeline definitions" runs `show_pipeline --all`)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Return the PR URL.

---

## Notes for the implementer

- **Single PR, single feature commit** (the design doc is its own commit, already on the branch). The restructure is internally consistent — splitting it across multiple commits would leave intermediate states with broken cross-references.
- **`set -e` is set at the top of `cli.sh`** — sourced modules inherit this. Don't introduce error-suppressing patterns in the new module.
- **Menu numbering** is via `select`'s `$REPLY` global — straightforward 1-N indices. The "Back" item is always last.
- **Don't reorder existing items within Install / Notifications / Intelligence / Updates** — only add to Install (2 new items at positions 11-12); the others stay byte-identical.
- **No changes to `bin/install.sh`, `bin/update.sh`, `bin/set_production.sh`, or any other non-CLI shell code.**
- **No changes to any Python code.** Pure shell-script work.
- **BATS tests** are smoke-only by design — they verify syntax, help text, and exit codes, NOT menu content beyond the help. Don't try to drive interactive menus via stdin pipes; that's outside what BATS reasonably tests for `select` loops.
- **`git rm` for deleted files** — don't just delete with `rm`. The `git rm` makes the deletion show up cleanly in the diff.